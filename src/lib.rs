use blake3::Hasher;
use chrono::Utc;
use ignore::{WalkBuilder, overrides::OverrideBuilder};
use memmap2::MmapOptions;
use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

// --- Constants ---
const PRESERVED_FILES: &[&str] = &[".veghignore", ".gitignore", ".dockerignore", ".npmignore"];
const CACHE_DIR: &str = ".veghcache";
const CACHE_FILE: &str = "index.json";
const SNAPSHOT_FORMAT_VERSION: &str = "2";
const VEGH_COMPAT_VERSION: &str = "0.3.0";

// --- Structs (Keep existing) ---
#[derive(Serialize, Deserialize)]
struct VeghMetadata {
    author: String,
    timestamp: i64,
    comment: String,
    tool_version: String,
    format_version: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct FileCacheEntry {
    size: u64,
    modified: u64,
}

#[derive(Serialize, Deserialize, Debug, Default)]
struct VeghCache {
    last_snapshot: i64,
    files: HashMap<String, FileCacheEntry>,
}

// --- Helper Functions (Keep existing) ---

fn get_cache_path(source: &Path) -> PathBuf {
    source.join(CACHE_DIR).join(CACHE_FILE)
}

fn load_cache(source: &Path) -> VeghCache {
    let cache_path = get_cache_path(source);
    if cache_path.exists() {
        if let Ok(file) = File::open(&cache_path) {
            if let Ok(cache) = serde_json::from_reader(file) {
                return cache;
            }
        }
        let _ = fs::remove_dir_all(source.join(CACHE_DIR));
    }
    VeghCache::default()
}

fn save_cache(source: &Path, cache: &VeghCache) -> std::io::Result<()> {
    let cache_dir = source.join(CACHE_DIR);
    if !cache_dir.exists() {
        fs::create_dir(&cache_dir)?;
    }
    let file = File::create(get_cache_path(source))?;
    serde_json::to_writer_pretty(file, cache)?;
    Ok(())
}

// --- Main PyFunctions ---

#[pyfunction]
#[pyo3(signature = (source, output, level=3, comment=None, include=None, exclude=None, no_cache=false))]
fn create_snap(
    source: String,
    output: String,
    level: i32,
    comment: Option<String>,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
    no_cache: bool,
) -> PyResult<usize> {
    let source_path = Path::new(&source);
    let output_path = Path::new(&output);
    let file = File::create(output_path).map_err(|e| PyIOError::new_err(e.to_string()))?;

    // Resolve absolute path to avoid recursive packing of output file
    let output_abs = fs::canonicalize(output_path).unwrap_or_else(|_| output_path.to_path_buf());

    let mut cache = if no_cache {
        VeghCache::default()
    } else {
        load_cache(source_path)
    };
    let mut new_cache_files = HashMap::new();

    let meta = VeghMetadata {
        author: "CodeTease (PyVegh)".to_string(),
        timestamp: Utc::now().timestamp(),
        comment: comment.unwrap_or_default(),
        tool_version: VEGH_COMPAT_VERSION.to_string(),
        format_version: SNAPSHOT_FORMAT_VERSION.to_string(),
    };
    let meta_json = serde_json::to_string_pretty(&meta).unwrap();

    let mut encoder = zstd::stream::write::Encoder::new(file, level)
        .map_err(|e| PyIOError::new_err(e.to_string()))?;

    // Enable multithreading for Zstd
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    encoder
        .multithread(workers as u32)
        .map_err(|e| PyIOError::new_err(format!("Zstd MT error: {}", e)))?;

    let mut tar = tar::Builder::new(encoder);

    // Add Metadata file
    let mut header = tar::Header::new_gnu();
    header.set_path(".vegh.json").unwrap();
    header.set_size(meta_json.len() as u64);
    header.set_mode(0o644);
    header.set_cksum();
    tar.append_data(&mut header, ".vegh.json", meta_json.as_bytes())
        .map_err(|e| PyIOError::new_err(e.to_string()))?;

    let mut count = 0;

    // Always include preserved config files if they exist
    for &name in PRESERVED_FILES {
        let p = source_path.join(name);
        if p.exists() {
            let mut f = File::open(&p).map_err(|e| PyIOError::new_err(e.to_string()))?;
            tar.append_file(name, &mut f)
                .map_err(|e| PyIOError::new_err(e.to_string()))?;
            count += 1;
        }
    }

    // --- FIX: Override Logic ---
    let mut override_builder = OverrideBuilder::new(source_path);

    if let Some(incs) = include {
        // Positive pattern = Whitelist (Include ONLY these)
        for pattern in incs {
            let _ = override_builder.add(&pattern);
        }
    }
    if let Some(excs) = exclude {
        // Negative pattern = Blacklist (Exclude these). MUST start with '!'
        for pattern in excs {
            let _ = override_builder.add(&format!("!{}", pattern));
        }
    }
    // Always exclude internal cache
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));

    let overrides = override_builder
        .build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES {
        builder.add_custom_ignore_filename(f);
    }

    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                // Avoid self-inclusion
                if let Ok(abs) = fs::canonicalize(path) {
                    if abs == output_abs {
                        continue;
                    }
                }

                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();

                // Skip files we already manually added
                if PRESERVED_FILES.contains(&name_str.as_str()) {
                    continue;
                }

                // Cache logic could go here (skipped for brevity in this context)
                let metadata = path
                    .metadata()
                    .map_err(|e| PyIOError::new_err(e.to_string()))?;
                let modified = metadata
                    .modified()
                    .unwrap_or(SystemTime::UNIX_EPOCH)
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs();
                let size = metadata.len();

                new_cache_files.insert(name_str, FileCacheEntry { size, modified });

                tar.append_path_with_name(path, name)
                    .map_err(|e| PyIOError::new_err(e.to_string()))?;
                count += 1;
            }
        }
    }

    cache.files = new_cache_files;
    cache.last_snapshot = Utc::now().timestamp();
    if !no_cache {
        let _ = save_cache(source_path, &cache);
    }

    let enc = tar.into_inner().unwrap();
    enc.finish()
        .map_err(|e| PyIOError::new_err(format!("Finalize error: {}", e)))?;

    Ok(count)
}

#[pyfunction]
#[pyo3(signature = (file_path, query, prefix=None, case_sensitive=true))]
fn search_snap(
    file_path: String,
    query: String,
    prefix: Option<String>,
    case_sensitive: bool,
) -> PyResult<Vec<(String, usize, String)>> {
    // Open the snapshot file
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;

    // Setup decompression and tar archive reading
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    let mut results = Vec::new();
    let prefix_str = prefix.unwrap_or_default();

    // Pre-calculate lowercase query for case-insensitive search to avoid re-allocation
    let query_lower = if !case_sensitive {
        query.to_lowercase()
    } else {
        String::new()
    };

    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(mut e) = entry {
                let path = e.path().unwrap().into_owned();
                let path_str = path.to_string_lossy().to_string();

                // 1. Filter by path scope (e.g., inside a specific folder in explore mode)
                // Also skip internal metadata file
                if !path_str.starts_with(&prefix_str) || path_str == ".vegh.json" {
                    continue;
                }

                // 2. Read content as text
                // Using read_to_string automatically handles UTF-8 validation.
                // If it fails (binary file), we simply skip it.
                let mut content = String::new();
                if e.read_to_string(&mut content).is_ok() {
                    for (i, line) in content.lines().enumerate() {
                        let is_match = if case_sensitive {
                            line.contains(&query)
                        } else {
                            // Simple case-insensitive check using std (no extra deps)
                            line.to_lowercase().contains(&query_lower)
                        };

                        if is_match {
                            // Truncate long lines for better CLI display
                            let display_line = if line.len() > 100 {
                                format!("{}...", &line[..100])
                            } else {
                                line.to_string()
                            };

                            // Return tuple: (File Path, Line Number, Content)
                            results.push((path_str.clone(), i + 1, display_line));
                        }
                    }
                }
            }
        }
    }
    Ok(results)
}

#[pyfunction]
#[pyo3(signature = (source, include=None, exclude=None))]
fn dry_run_snap(
    source: String,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
) -> PyResult<Vec<(String, u64)>> {
    let source_path = Path::new(&source);
    let mut results = Vec::new();

    for &name in PRESERVED_FILES {
        let p = source_path.join(name);
        if p.exists() {
            if let Ok(meta) = fs::metadata(&p) {
                results.push((name.to_string(), meta.len()));
            }
        }
    }

    // --- FIX: Override Logic ---
    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(incs) = include {
        for pattern in incs {
            let _ = override_builder.add(&pattern);
        }
    }
    if let Some(excs) = exclude {
        // Exclude must start with '!'
        for pattern in excs {
            let _ = override_builder.add(&format!("!{}", pattern));
        }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));

    let overrides = override_builder
        .build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES {
        builder.add_custom_ignore_filename(f);
    }
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();
                if PRESERVED_FILES.contains(&name_str.as_str()) {
                    continue;
                }
                let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
                results.push((name_str, size));
            }
        }
    }

    Ok(results)
}

#[pyfunction]
#[pyo3(signature = (file_path, out_dir, include=None, flatten=false))]
fn restore_snap(
    file_path: String,
    out_dir: String,
    include: Option<Vec<String>>,
    flatten: bool,
) -> PyResult<()> {
    let out = Path::new(&out_dir);
    if !out.exists() {
        fs::create_dir_all(out).map_err(|e| PyIOError::new_err(e.to_string()))?;
    }

    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    for entry in archive
        .entries()
        .map_err(|e| PyIOError::new_err(e.to_string()))?
    {
        let mut entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        let path = entry.path().unwrap().into_owned();
        if path.to_string_lossy() == ".vegh.json" {
            continue;
        }

        if let Some(ref incs) = include {
            let mut matched = false;
            for pattern in incs {
                if path.starts_with(Path::new(pattern)) {
                    matched = true;
                    break;
                }
            }
            if !matched {
                continue;
            }
        }

        if flatten {
            if let Some(file_name) = path.file_name() {
                let target = out.join(file_name);
                entry
                    .unpack(target)
                    .map_err(|e| PyIOError::new_err(e.to_string()))?;
            }
        } else {
            entry
                .unpack_in(out)
                .map_err(|e| PyIOError::new_err(e.to_string()))?;
        }
    }
    Ok(())
}

#[pyfunction]
fn list_files(file_path: String) -> PyResult<Vec<String>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    let mut files = Vec::new();
    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(e) = entry {
                if let Ok(p) = e.path() {
                    files.push(p.to_string_lossy().to_string());
                }
            }
        }
    }
    Ok(files)
}

#[pyfunction]
fn check_integrity(file_path: String) -> PyResult<String> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;

    let hash = if let Ok(mmap) = unsafe { MmapOptions::new().map(&file) } {
        let mut hasher = Hasher::new();
        hasher.update_rayon(&mmap);
        hasher.finalize().to_hex().to_string()
    } else {
        let mut f = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
        let mut hasher = Hasher::new();
        std::io::copy(&mut f, &mut hasher).map_err(|e| PyIOError::new_err(e.to_string()))?;
        hasher.finalize().to_hex().to_string()
    };

    Ok(hash)
}

#[pyfunction]
fn get_metadata(file_path: String) -> PyResult<String> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(mut e) = entry {
                if let Ok(p) = e.path() {
                    if p.to_string_lossy() == ".vegh.json" {
                        let mut content = String::new();
                        e.read_to_string(&mut content)
                            .map_err(|e| PyIOError::new_err(e.to_string()))?;
                        return Ok(content);
                    }
                }
            }
        }
    }
    Err(PyValueError::new_err("Metadata not found in snapshot"))
}

#[pyfunction]
fn count_locs(file_path: String) -> PyResult<Vec<(String, usize)>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    let mut results = Vec::new();

    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(mut e) = entry {
                let path = e.path().unwrap().into_owned();
                let path_str = path.to_string_lossy().to_string();

                if path_str == ".vegh.json" {
                    continue;
                }
                let mut content = String::new();
                match e.read_to_string(&mut content) {
                    Ok(_) => {
                        if content.contains('\0') {
                            results.push((path_str, 0));
                        } else {
                            results.push((path_str, content.lines().count()));
                        }
                    }
                    Err(_) => {
                        results.push((path_str, 0));
                    }
                }
            }
        }
    }
    Ok(results)
}

#[pyfunction]
#[pyo3(signature = (source, exclude=None))]
fn scan_locs_dir(source: String, exclude: Option<Vec<String>>) -> PyResult<Vec<(String, usize)>> {
    let source_path = Path::new(&source);
    let mut results = Vec::new();

    // --- FIX: Override Logic ---
    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(excs) = exclude {
        // Exclude MUST start with '!' to be an ignore pattern
        for pattern in excs {
            let _ = override_builder.add(&format!("!{}", pattern));
        }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));

    let overrides = override_builder
        .build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES {
        builder.add_custom_ignore_filename(f);
    }
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();
                if PRESERVED_FILES.contains(&name_str.as_str()) {
                    continue;
                }

                let count = if let Ok(mut file) = File::open(path) {
                    // Check binary (Header check)
                    let mut buffer = [0; 1024];
                    let chunk_size = file.read(&mut buffer).unwrap_or(0);

                    if buffer[..chunk_size].contains(&0) {
                        0
                    } else {
                        // Rewind to start
                        if file.seek(SeekFrom::Start(0)).is_ok() {
                            let reader = BufReader::new(file);
                            reader.lines().count()
                        } else {
                            0
                        }
                    }
                } else {
                    0
                };

                results.push((name_str, count));
            }
        }
    }
    Ok(results)
}

#[pyfunction]
fn cat_file(file_path: String, target_file: String) -> PyResult<Vec<u8>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    for entry in archive
        .entries()
        .map_err(|e| PyIOError::new_err(e.to_string()))?
    {
        let mut entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        let path = entry.path().unwrap().into_owned();
        let path_str = path.to_string_lossy().to_string();

        if path_str == target_file {
            let mut content = Vec::new();
            entry
                .read_to_end(&mut content)
                .map_err(|e| PyIOError::new_err(e.to_string()))?;
            return Ok(content);
        }
    }
    Err(PyValueError::new_err(format!(
        "File '{}' not found in snapshot",
        target_file
    )))
}

#[pyfunction]
fn list_files_details(file_path: String) -> PyResult<Vec<(String, u64)>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    let mut results = Vec::new();
    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(e) = entry {
                let size = e.size();
                if let Ok(p) = e.path() {
                    results.push((p.to_string_lossy().to_string(), size));
                }
            }
        }
    }
    Ok(results)
}

// --- NEW FUNCTION: VEGH PROMPT CONTEXT ---

#[pyfunction]
#[pyo3(signature = (source, include=None, exclude=None))]
fn get_context_xml(
    source: String,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
) -> PyResult<String> {
    let source_path = Path::new(&source);

    // Initialize XML output with root tag
    let mut xml_output = String::from("<codebase>\n");

    // --- FIX: Override Logic ---
    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(incs) = include {
        for pattern in incs {
            let _ = override_builder.add(&pattern);
        }
    }
    if let Some(excs) = exclude {
        // Exclude must be negated (!) to act as an ignore pattern
        for pattern in excs {
            let _ = override_builder.add(&format!("!{}", pattern));
        }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));

    let overrides = override_builder
        .build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    // Setup WalkBuilder
    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES {
        builder.add_custom_ignore_filename(f);
    }

    // Crucial: Use hidden(true) and git_ignore(true) to respect .gitignore
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                // Get relative path
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();

                // Skip internal config files unless explicitly asked
                if PRESERVED_FILES.contains(&name_str.as_str()) {
                    continue;
                }

                // Check for binary content (Read first 1KB)
                if let Ok(mut file) = File::open(path) {
                    let mut buffer = [0; 1024];
                    let chunk_size = file.read(&mut buffer).unwrap_or(0);

                    // If contains null byte, assume binary and skip
                    if buffer[..chunk_size].contains(&0) {
                        continue;
                    }
                }

                // Read full content as text
                if let Ok(content) = fs::read_to_string(path) {
                    xml_output.push_str(&format!(
                        "  <file path=\"{}\">\n    <![CDATA[\n{}\n    ]]>\n  </file>\n",
                        name_str, content
                    ));
                }
            }
        }
    }

    xml_output.push_str("</codebase>");
    Ok(xml_output)
}

#[pymodule]
#[pyo3(name = "_core")]
fn pyvegh_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(create_snap, m)?)?;
    m.add_function(wrap_pyfunction!(dry_run_snap, m)?)?;
    m.add_function(wrap_pyfunction!(restore_snap, m)?)?;
    m.add_function(wrap_pyfunction!(list_files, m)?)?;
    m.add_function(wrap_pyfunction!(check_integrity, m)?)?;
    m.add_function(wrap_pyfunction!(get_metadata, m)?)?;
    m.add_function(wrap_pyfunction!(count_locs, m)?)?;
    m.add_function(wrap_pyfunction!(scan_locs_dir, m)?)?;
    m.add_function(wrap_pyfunction!(cat_file, m)?)?;
    m.add_function(wrap_pyfunction!(list_files_details, m)?)?;
    m.add_function(wrap_pyfunction!(get_context_xml, m)?)?;
    m.add_function(wrap_pyfunction!(search_snap, m)?)?;
    Ok(())
}
