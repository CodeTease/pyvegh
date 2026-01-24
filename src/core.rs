use anyhow::{Context, Result};
use chrono::Utc;
use crossbeam_channel::bounded;
use ignore::{WalkBuilder, overrides::OverrideBuilder};
use indicatif::{ProgressBar, ProgressStyle}; // Added for smooth UI
use serde::{Deserialize, Serialize};
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::SystemTime;

use crate::hash::{compute_chunks, compute_file_hash, compute_sparse_hash};
use crate::storage::{
    CACHE_DIR, CacheDB, FileCacheEntry, ManifestEntry, SnapshotManifest, StoredChunk,
};

// --- CONSTANTS from Vegh 0.4.0 ---
const PRESERVED_FILES: &[&str] = &[".veghignore", ".gitignore", ".npmignore", ".dockerignore"];
const SNAPSHOT_FORMAT_VERSION: &str = "3"; // Synced with CLI
const CDC_THRESHOLD: u64 = 1024 * 1024; // 1MB
const CDC_AVG_SIZE: usize = 1024 * 1024; // 1MB
const CACHE_RETENTION_SEC: u64 = 30 * 24 * 60 * 60; // 30 Days
const BATCH_COMMIT_SIZE: usize = 1000;

#[derive(Serialize, Deserialize, Debug)]
pub struct VeghMetadata {
    pub author: String,
    pub timestamp: i64,
    #[serde(default)]
    pub timestamp_human: Option<String>,
    pub comment: String,
    pub tool_version: String,
    pub format_version: String,
}

// Pipeline Messages
enum WorkerResult {
    Processed(ProcessedMessage),
    Error(String),
}

struct ProcessedMessage {
    path_str: String,
    abs_path: PathBuf,
    metadata_info: MetadataInfo,
    entry: FileCacheEntry,
    data_action: DataAction,
    is_cached_hit: bool,
}

struct MetadataInfo {
    size: u64,
    modified: u64,
    mode: u32,
}

enum DataAction {
    Cached,
    WriteFile(Vec<u8>),
    WriteChunks(Vec<StoredChunk>),
}

// --- Main Packing Logic ---

pub fn create_snap_logic(
    source: &Path,
    output: &Path,
    level: i32,
    comment: Option<String>,
    include: Vec<String>,
    exclude: Vec<String>,
    no_cache: bool,
    verbose: bool, // Added flag to control UI output
) -> Result<usize> {
    let running = Arc::new(AtomicBool::new(true));

    // Initialize Redb Cache
    let mut cache_db = CacheDB::open(source)?;

    let file = File::create(output).context("Output file creation failed")?;
    let output_abs = fs::canonicalize(output).unwrap_or(output.to_path_buf());

    // Prepare Metadata
    let meta = VeghMetadata {
        author: "CodeTease (PyVegh)".to_string(),
        timestamp: Utc::now().timestamp(),
        timestamp_human: Some(Utc::now().to_rfc3339()),
        comment: comment.unwrap_or_default(),
        tool_version: env!("CARGO_PKG_VERSION").to_string(),
        format_version: SNAPSHOT_FORMAT_VERSION.to_string(),
    };
    let meta_json = serde_json::to_string_pretty(&meta)?;

    let mut encoder = zstd::stream::write::Encoder::new(file, level)?;
    let num_threads = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    encoder.multithread(num_threads as u32)?;

    let mut tar = tar::Builder::new(encoder);

    // Write Meta (Hidden Header)
    let mut header = tar::Header::new_gnu();
    header.set_path(".vegh.json")?;
    header.set_size(meta_json.len() as u64);
    header.set_mode(0o644);
    header.set_cksum();
    tar.append_data(&mut header, ".vegh.json", meta_json.as_bytes())?;

    // --- SETUP PROGRESS BAR (Only if verbose is true) ---
    // This runs completely in Rust, avoiding Python GIL overhead for thousands of updates.
    let pb = if verbose {
        let p = ProgressBar::new_spinner();
        p.set_style(
            ProgressStyle::default_spinner()
                .template("{spinner:.green} {msg}")
                .unwrap(),
        );
        Some(p)
    } else {
        None
    };

    // 1. Setup Channels
    let (path_tx, path_rx) = bounded::<PathBuf>(1024);
    let (res_tx, res_rx) = bounded::<WorkerResult>(1024);

    // 2. Scanner Thread
    let source_buf = source.to_path_buf();
    let source_buf_for_scan = source_buf.clone();
    let path_tx_for_scan = path_tx.clone();
    let r_scan = running.clone();

    // Reconstruct ignore logic
    let mut override_builder = OverrideBuilder::new(&source_buf);
    for pattern in include {
        let _ = override_builder.add(&pattern);
    }
    for pattern in exclude {
        let _ = override_builder.add(&format!("!{}", pattern));
    }

    // FIX: Override does not implement Default, so handle error manually
    let overrides = override_builder
        .build()
        .unwrap_or_else(|_| OverrideBuilder::new(&source_buf).build().unwrap());

    let scanner_handle = std::thread::spawn(move || {
        let mut builder = WalkBuilder::new(&source_buf_for_scan);
        for &f in PRESERVED_FILES {
            builder.add_custom_ignore_filename(f);
        }
        // Exclude internal cache
        builder.filter_entry(|entry| !entry.path().to_string_lossy().contains(CACHE_DIR));
        builder.hidden(true).git_ignore(true).overrides(overrides);

        for result in builder.build() {
            if !r_scan.load(Ordering::SeqCst) {
                break;
            }
            if let Ok(entry) = result
                && entry.file_type().map(|ft| ft.is_file()).unwrap_or(false)
            {
                // Check against output file recursion
                if let Ok(abs) = fs::canonicalize(entry.path())
                    && abs == output_abs
                {
                    continue;
                }
                if path_tx_for_scan.send(entry.path().to_path_buf()).is_err() {
                    break;
                }
            }
        }
    });

    // 3. Worker Threads
    let mut worker_handles = Vec::new();
    let cache_reader = cache_db.reader();
    let written_blobs = Arc::new(dashmap::DashMap::new());
    let written_blobs_shared = written_blobs.clone();

    for _ in 0..num_threads {
        let rx = path_rx.clone();
        let tx = res_tx.clone();
        let reader = cache_reader.clone();
        let blobs = written_blobs_shared.clone();
        let src_root = source_buf.clone();
        let r_worker = running.clone();
        let no_cache_flag = no_cache;

        worker_handles.push(std::thread::spawn(move || {
            while let Ok(path) = rx.recv() {
                if !r_worker.load(Ordering::SeqCst) {
                    break;
                }

                let process_res = (|| -> Result<ProcessedMessage> {
                    let name = path.strip_prefix(&src_root).unwrap_or(&path);
                    let name_str = name.to_string_lossy().to_string();
                    let metadata = path.metadata()?;
                    let size = metadata.len();
                    let modified = metadata
                        .modified()
                        .unwrap_or(SystemTime::UNIX_EPOCH)
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs();

                    #[cfg(unix)]
                    let (mode, inode, device_id, ctime_sec, ctime_nsec) = {
                        use std::os::unix::fs::MetadataExt;
                        (
                            metadata.mode(),
                            metadata.ino(),
                            metadata.dev(),
                            metadata.ctime(),
                            metadata.ctime_nsec() as u32,
                        )
                    };
                    #[cfg(not(unix))]
                    let (mode, inode, device_id, ctime_sec, ctime_nsec) = (0o644, 0, 0, 0, 0);

                    let now_ts = SystemTime::now()
                        .duration_since(SystemTime::UNIX_EPOCH)
                        .unwrap()
                        .as_secs();
                    let use_cdc = size > CDC_THRESHOLD;

                    let cached_entry_opt = if no_cache_flag {
                        None
                    } else {
                        reader.get(&name_str)?
                    };

                    let (hash, chunks_info, is_cached_hit) =
                        if let Some(ref cached_entry) = cached_entry_opt {
                            let is_hit = cached_entry.modified == modified
                                && cached_entry.size == size
                                && cached_entry.inode == inode
                                && (cached_entry.device_id == 0
                                    || device_id == 0
                                    || cached_entry.device_id == device_id)
                                && cached_entry.hash.is_some();

                            if is_hit {
                                if use_cdc {
                                    if let Ok(Some(chunks)) = cached_entry.get_chunks() {
                                        (cached_entry.hash.unwrap(), Some(chunks), true)
                                    } else {
                                        let (h, chunks) = compute_chunks(&path, CDC_AVG_SIZE)?;
                                        let stored_chunks: Vec<StoredChunk> = chunks
                                            .into_iter()
                                            .map(|c| StoredChunk {
                                                hash: c.hash,
                                                offset: c.offset as u64,
                                                length: c.length as u32,
                                            })
                                            .collect();
                                        (h, Some(stored_chunks), false)
                                    }
                                } else {
                                    (cached_entry.hash.unwrap(), None, true)
                                }
                            } else if use_cdc {
                                let (h, chunks) = compute_chunks(&path, CDC_AVG_SIZE)?;
                                let stored_chunks: Vec<StoredChunk> = chunks
                                    .into_iter()
                                    .map(|c| StoredChunk {
                                        hash: c.hash,
                                        offset: c.offset as u64,
                                        length: c.length as u32,
                                    })
                                    .collect();
                                (h, Some(stored_chunks), false)
                            } else {
                                let h = compute_file_hash(&path)?;
                                (h, None, false)
                            }
                        } else if use_cdc {
                            let (h, chunks) = compute_chunks(&path, CDC_AVG_SIZE)?;
                            let stored_chunks: Vec<StoredChunk> = chunks
                                .into_iter()
                                .map(|c| StoredChunk {
                                    hash: c.hash,
                                    offset: c.offset as u64,
                                    length: c.length as u32,
                                })
                                .collect();
                            (h, Some(stored_chunks), false)
                        } else {
                            let h = compute_file_hash(&path)?;
                            (h, None, false)
                        };

                    let mut data_action = DataAction::Cached;
                    if let Some(chunks) = chunks_info.clone() {
                        let mut chunks_to_write = Vec::new();
                        for c in chunks {
                            let hex_h = hex::encode(c.hash);
                            if !blobs.contains_key(&hex_h) {
                                chunks_to_write.push(c);
                            }
                        }
                        if !chunks_to_write.is_empty() {
                            data_action = DataAction::WriteChunks(chunks_to_write);
                        }
                    } else {
                        let hex_h = hex::encode(hash);
                        if !blobs.contains_key(&hex_h) {
                            data_action = DataAction::WriteFile(hash.to_vec());
                        }
                    }

                    let mut entry = FileCacheEntry {
                        size,
                        modified,
                        inode,
                        device_id,
                        ctime_sec,
                        ctime_nsec,
                        last_seen: now_ts,
                        hash: Some(hash),
                        chunks_compressed: None,
                        sparse_hash: None,
                    };

                    if !is_cached_hit {
                        entry.sparse_hash = compute_sparse_hash(&path, size).ok();
                    } else if let Some(old) = cached_entry_opt.and_then(|e| e.sparse_hash) {
                        entry.sparse_hash = Some(old);
                    }

                    if let Some(chunks) = chunks_info {
                        entry.set_chunks(chunks)?;
                    }

                    Ok(ProcessedMessage {
                        path_str: name_str,
                        abs_path: path,
                        metadata_info: MetadataInfo {
                            size,
                            modified,
                            mode,
                        },
                        entry,
                        data_action,
                        is_cached_hit,
                    })
                })();

                match process_res {
                    Ok(msg) => {
                        let _ = tx.send(WorkerResult::Processed(msg));
                    }
                    Err(e) => {
                        let _ = tx.send(WorkerResult::Error(e.to_string()));
                    }
                }
            }
        }));
    }

    // 4. Writer Loop
    drop(path_tx);
    drop(res_tx);

    let mut count = 0;
    let mut dedup_count = 0;
    let mut cache_hit_count = 0;
    let mut manifest = SnapshotManifest::default();
    let mut batch_counter = 0;

    while let Ok(msg) = res_rx.recv() {
        match msg {
            WorkerResult::Error(e) => {
                if let Some(ref p) = pb {
                    p.println(format!("⚠️ Error: {}", e));
                } else {
                    eprintln!("Error: {}", e);
                }
            }
            WorkerResult::Processed(pm) => {
                if pm.is_cached_hit {
                    cache_hit_count += 1;
                }

                // Update Progress Bar UI
                if let Some(ref p) = pb {
                    match pm.data_action {
                        DataAction::Cached => {
                            p.set_message(format!("Dedup: {}", pm.path_str));
                        }
                        DataAction::WriteFile(_) => {
                            p.set_message(format!("Writing: {}", pm.path_str));
                        }
                        DataAction::WriteChunks(_) => {
                            p.set_message(format!("Chunking: {}", pm.path_str));
                        }
                    }
                }

                match pm.data_action {
                    DataAction::Cached => {
                        dedup_count += 1;
                    }
                    DataAction::WriteFile(hash_bytes) => {
                        let hash_hex = hex::encode(&hash_bytes);
                        if !written_blobs.contains_key(&hash_hex) {
                            let blob_path = format!("blobs/{}", hash_hex);
                            let mut f = File::open(&pm.abs_path)?;
                            tar.append_file(&blob_path, &mut f)?;
                            written_blobs.insert(hash_hex, ());
                        } else {
                            // If it was already in written_blobs (from another file), count as dedup
                            dedup_count += 1;
                            if let Some(ref p) = pb {
                                p.set_message(format!("Dedup (Blob): {}", pm.path_str));
                            }
                        }
                    }
                    DataAction::WriteChunks(chunks) => {
                        let mut f = File::open(&pm.abs_path)?;
                        let mut any_written = false;
                        for chunk in chunks {
                            let chunk_hex = hex::encode(chunk.hash);
                            if !written_blobs.contains_key(&chunk_hex) {
                                let blob_path = format!("blobs/{}", chunk_hex);
                                f.seek(SeekFrom::Start(chunk.offset))?;
                                let mut chunk_buf = vec![0u8; chunk.length as usize];
                                f.read_exact(&mut chunk_buf)?;

                                let mut header = tar::Header::new_gnu();
                                header.set_path(&blob_path)?;
                                header.set_size(chunk.length as u64);
                                header.set_mode(0o644);
                                header.set_cksum();
                                tar.append_data(&mut header, &blob_path, &chunk_buf[..])?;
                                written_blobs.insert(chunk_hex, ());
                                any_written = true;
                            }
                        }
                        if !any_written {
                            dedup_count += 1;
                            if let Some(ref p) = pb {
                                p.set_message(format!("Dedup (Chunks): {}", pm.path_str));
                            }
                        }
                    }
                }

                cache_db.insert(&pm.path_str, &pm.entry)?;

                let chunk_hashes_hex: Option<Vec<String>> = pm
                    .entry
                    .get_chunks()
                    .ok()
                    .flatten()
                    .map(|v| v.iter().map(|c| hex::encode(c.hash)).collect());

                manifest.entries.push(ManifestEntry {
                    path: pm.path_str,
                    hash: hex::encode(pm.entry.hash.unwrap_or_default()),
                    size: pm.metadata_info.size,
                    modified: pm.metadata_info.modified,
                    mode: pm.metadata_info.mode,
                    chunks: chunk_hashes_hex,
                });

                count += 1;
                batch_counter += 1;
                if batch_counter >= BATCH_COMMIT_SIZE {
                    cache_db.commit_batch()?;
                    batch_counter = 0;
                }
            }
        }
    }

    let _ = scanner_handle.join();
    for h in worker_handles {
        let _ = h.join();
    }

    if let Some(p) = pb {
        p.finish_with_message(format!(
            "Packed {} files ({} cache hits, {} deduped).",
            count, cache_hit_count, dedup_count
        ));
    }

    let manifest_json = serde_json::to_string_pretty(&manifest)?;
    let mut header = tar::Header::new_gnu();
    header.set_path("manifest.json")?;
    header.set_size(manifest_json.len() as u64);
    header.set_mode(0o644);
    header.set_cksum();
    tar.append_data(&mut header, "manifest.json", manifest_json.as_bytes())?;

    if !no_cache {
        let _ = cache_db.garbage_collect(CACHE_RETENTION_SEC);
        let _ = cache_db.commit();
    }

    let zstd_encoder = tar.into_inner()?;
    zstd_encoder.finish()?;

    Ok(count)
}

pub fn restore_snap_logic(
    input: &Path,
    out_dir: &Path,
    include: Option<Vec<String>>,
    flatten: bool,
) -> Result<()> {
    if !out_dir.exists() {
        fs::create_dir_all(out_dir)?;
    }

    let file = File::open(input).context("Open failed")?;
    let decoder = zstd::stream::read::Decoder::new(file)?;
    let mut archive = tar::Archive::new(decoder);

    archive.unpack(out_dir)?;

    let manifest_path = out_dir.join("manifest.json");
    if !manifest_path.exists() {
        return Ok(());
    }

    let manifest_file = File::open(&manifest_path)?;
    let manifest: SnapshotManifest = serde_json::from_reader(manifest_file)?;
    let blobs_dir = out_dir.join("blobs");

    for entry in manifest.entries {
        if let Some(ref patterns) = include {
            let mut matched = false;
            for p in patterns {
                if entry.path.starts_with(p) {
                    matched = true;
                    break;
                }
            }
            if !matched {
                continue;
            }
        }

        let dest_path = if flatten {
            out_dir.join(Path::new(&entry.path).file_name().unwrap())
        } else {
            out_dir.join(&entry.path)
        };

        if let Some(parent) = dest_path.parent() {
            fs::create_dir_all(parent)?;
        }

        let mut dest_file = File::create(&dest_path)?;
        let chunk_hashes = entry.chunks.unwrap_or_else(|| vec![entry.hash.clone()]);

        for chunk_hash in chunk_hashes {
            let blob_path = blobs_dir.join(&chunk_hash);
            if blob_path.exists() {
                let mut blob_file = File::open(&blob_path)?;
                std::io::copy(&mut blob_file, &mut dest_file)?;
            }
        }

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let permissions = fs::Permissions::from_mode(entry.mode);
            fs::set_permissions(&dest_path, permissions)?;
        }
    }

    let _ = fs::remove_file(manifest_path);
    let _ = fs::remove_dir_all(blobs_dir);
    let _ = fs::remove_file(out_dir.join(".vegh.json"));

    Ok(())
}
