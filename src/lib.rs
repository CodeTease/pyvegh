use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, Read, Seek, SeekFrom};
use std::path::Path;

// Declare modules so they are available to the crate
pub mod core;
pub mod hash;
pub mod storage;

use crate::core::{create_snap_logic, restore_snap_logic};
use crate::storage::{CACHE_DIR, SnapshotManifest};
use ignore::{WalkBuilder, overrides::OverrideBuilder};

// Constants synced with core/storage
const PRESERVED_FILES: &[&str] = &[".veghignore", ".gitignore", ".npmignore", ".dockerignore"];

// --- Helper Functions (Internal) ---

/// Helper to load snapshot content (works for both V2 legacy and V3 blobs).
fn load_snapshot_data(
    file_path: &Path,
    filter_fn: impl Fn(&str) -> bool,
) -> Result<Vec<(String, Vec<u8>)>, std::io::Error> {
    let file = File::open(file_path)?;
    let decoder = zstd::stream::read::Decoder::new(file)?;
    let mut archive = tar::Archive::new(decoder);

    let mut blobs: HashMap<String, Vec<u8>> = HashMap::new();
    let mut manifest_opt: Option<SnapshotManifest> = None;
    let mut legacy_files: Vec<(String, Vec<u8>)> = Vec::new();

    // Pass 1: Read everything into memory (Blobs & Manifest)
    for entry in archive.entries()? {
        let mut entry = entry?;
        let path = entry.path()?.to_string_lossy().to_string();

        if path == "manifest.json" {
            // Found the treasure map!
            if let Ok(manifest) = serde_json::from_reader::<_, SnapshotManifest>(&mut entry) {
                manifest_opt = Some(manifest);
            }
        } else if path.starts_with("blobs/") {
            // It's a chunk of data, store it.
            let hash = path.strip_prefix("blobs/").unwrap_or(&path).to_string();
            let mut data = Vec::new();
            entry.read_to_end(&mut data)?;
            blobs.insert(hash, data);
        } else if path != ".vegh.json" {
            // Likely a V2 file or just a loose file
            if filter_fn(&path) {
                let mut data = Vec::new();
                entry.read_to_end(&mut data)?;
                legacy_files.push((path, data));
            }
        }
    }

    // Pass 2: Reconstruct files if it's V3
    if let Some(manifest) = manifest_opt {
        let mut reconstructed = Vec::new();
        for entry in manifest.entries {
            if !filter_fn(&entry.path) {
                continue;
            }

            let mut content = Vec::new();
            // Default to using the file hash if chunks are missing (backward compat / single chunk)
            let chunks = entry.chunks.unwrap_or_else(|| vec![entry.hash.clone()]);

            let mut valid = true;
            for chunk_hash in chunks {
                if let Some(blob_data) = blobs.get(&chunk_hash) {
                    content.extend_from_slice(blob_data);
                } else {
                    // Missing blob? That's bad.
                    valid = false;
                    break;
                }
            }

            if valid {
                reconstructed.push((entry.path, content));
            }
        }
        Ok(reconstructed)
    } else {
        // Fallback for V2 (No manifest found)
        Ok(legacy_files)
    }
}

// --- FIX 1: Add helper to read snapshot files as text for SLOC analysis ---
#[pyfunction]
fn read_snapshot_text(file_path: String) -> PyResult<Vec<(String, String)>> {
    let path = Path::new(&file_path);

    // Reuse existing load_snapshot_data logic (Handles both V2 and V3/Blobs)
    let files = load_snapshot_data(path, |_| true)
        .map_err(|e| PyIOError::new_err(format!("Failed to read snapshot: {}", e)))?;

    let mut results = Vec::new();
    for (name, content) in files {
        // Skip binary files (check for null byte)
        if content.contains(&0) {
            continue;
        }
        // Convert to string (lossy to handle potential non-UTF8 comments safely)
        let text = String::from_utf8_lossy(&content).to_string();
        results.push((name, text));
    }

    Ok(results)
}

// --- PyFunctions Wrappers ---

#[pyfunction]
#[pyo3(signature = (source, output, level=3, comment=None, include=None, exclude=None, no_cache=false, verbose=true))]
fn create_snap(
    source: String,
    output: String,
    level: i32,
    comment: Option<String>,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
    no_cache: bool,
    verbose: bool, // Added flag to control UI output from Python
) -> PyResult<usize> {
    let source_path = Path::new(&source);
    let output_path = Path::new(&output);

    create_snap_logic(
        source_path,
        output_path,
        level,
        comment,
        include.unwrap_or_default(),
        exclude.unwrap_or_default(),
        no_cache,
        verbose,
    )
    .map_err(|e| PyIOError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (file_path, out_dir, include=None, flatten=false))]
fn restore_snap(
    file_path: String,
    out_dir: String,
    include: Option<Vec<String>>,
    flatten: bool,
) -> PyResult<()> {
    let input_path = Path::new(&file_path);
    let output_path = Path::new(&out_dir);

    restore_snap_logic(input_path, output_path, include, flatten)
        .map_err(|e| PyIOError::new_err(e.to_string()))
}

// --- Utility Functions ---

#[pyfunction]
fn list_files(file_path: String) -> PyResult<Vec<String>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    let mut files = Vec::new();

    // We can't use load_snapshot_data here efficiently because we just want names.
    // So we iterate. If we find manifest at the end, we use it.
    if let Ok(entries) = archive.entries() {
        for mut e in entries.flatten() {
            let path = e.path().unwrap().into_owned();
            let path_str = path.to_string_lossy().to_string();

            if path_str == "manifest.json" {
                let mut content = String::new();
                if e.read_to_string(&mut content).is_ok()
                    && let Ok(manifest) = serde_json::from_str::<SnapshotManifest>(&content)
                {
                    files = manifest
                        .entries
                        .into_iter()
                        .map(|entry| entry.path)
                        .collect();
                }
            } else if !path_str.starts_with("blobs/") && path_str != ".vegh.json" {
                files.push(path_str);
            }
        }
    }
    // If manifest was found (V3), 'files' was overwritten with manifest entries.
    // If not (V2), 'files' contains the tar entries.
    Ok(files)
}

#[pyfunction]
fn get_metadata(file_path: String) -> PyResult<String> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    if let Ok(entries) = archive.entries() {
        for entry in entries {
            if let Ok(mut e) = entry
                && let Ok(p) = e.path()
                && p.to_string_lossy() == ".vegh.json"
            {
                let mut content = String::new();
                e.read_to_string(&mut content)
                    .map_err(|e| PyIOError::new_err(e.to_string()))?;
                return Ok(content);
            }
        }
    }
    Err(PyValueError::new_err("Metadata not found in snapshot"))
}

#[pyfunction]
fn check_integrity(file_path: String) -> PyResult<String> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let mut hasher = blake3::Hasher::new();

    if let Ok(mmap) = unsafe { memmap2::MmapOptions::new().map(&file) } {
        hasher.update_rayon(&mmap);
    } else {
        let mut f = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
        std::io::copy(&mut f, &mut hasher).map_err(|e| PyIOError::new_err(e.to_string()))?;
    }

    Ok(hasher.finalize().to_hex().to_string())
}

#[pyfunction]
fn count_locs(file_path: String) -> PyResult<Vec<(String, usize)>> {
    let path = Path::new(&file_path);

    // Load all files (no filter)
    let files = load_snapshot_data(path, |_| true)
        .map_err(|e| PyIOError::new_err(format!("Failed to read snapshot: {}", e)))?;

    let mut results = Vec::new();
    for (name, content) in files {
        if let Ok(text) = String::from_utf8(content) {
            results.push((name, text.lines().count()));
        } else {
            results.push((name, 0));
        }
    }

    Ok(results)
}

// --- Directory Scanners & Hybrid Logic ---

#[pyfunction]
#[pyo3(signature = (source, include=None, exclude=None))]
fn dry_run_snap(
    source: String,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
) -> PyResult<Vec<(String, u64)>> {
    let source_path = Path::new(&source);
    let mut results = Vec::new();

    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(incs) = include {
        for pattern in incs {
            let _ = override_builder.add(&pattern);
        }
    }
    if let Some(excs) = exclude {
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
    builder.filter_entry(|entry| !entry.path().to_string_lossy().contains(CACHE_DIR));

    for entry in builder.build().flatten() {
        let path = entry.path();
        if path.is_file() {
            let name = path.strip_prefix(source_path).unwrap_or(path);
            let name_str = name.to_string_lossy().to_string();
            let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
            results.push((name_str, size));
        }
    }

    Ok(results)
}

#[pyfunction]
#[pyo3(signature = (source, include=None, exclude=None))]
fn get_context_xml(
    source: String,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
) -> PyResult<String> {
    let source_path = Path::new(&source);
    let mut xml_output = String::from("<codebase>\n");

    // Case 1: Source is a Snapshot File
    if source_path.is_file() {
        let files = load_snapshot_data(source_path, |path| {
            if let Some(incs) = &include {
                let mut matched = false;
                for inc in incs {
                    if path.contains(inc) {
                        matched = true;
                        break;
                    }
                }
                if !matched {
                    return false;
                }
            }
            if let Some(excs) = &exclude {
                for exc in excs {
                    if path.contains(exc) {
                        return false;
                    }
                }
            }
            true
        })
        .map_err(|e| PyIOError::new_err(format!("Failed to read snapshot: {}", e)))?;

        for (name, content) in files {
            if content.contains(&0) {
                continue;
            }

            let content_str = String::from_utf8_lossy(&content);
            xml_output.push_str(&format!(
                "  <file path=\"{}\">\n    <![CDATA[\n{}\n    ]]>\n  </file>\n",
                name, content_str
            ));
        }

    // Case 2: Source is a Directory
    } else {
        let mut override_builder = OverrideBuilder::new(source_path);
        if let Some(incs) = include {
            for pattern in incs {
                let _ = override_builder.add(&pattern);
            }
        }
        if let Some(excs) = exclude {
            for pattern in excs {
                let _ = override_builder.add(&format!("!{}", pattern));
            }
        }
        let _ = override_builder.add(&format!("!{}", CACHE_DIR));
        let overrides = override_builder
            .build()
            .map_err(|e| PyIOError::new_err(e.to_string()))?;

        let mut builder = WalkBuilder::new(source_path);
        for &f in PRESERVED_FILES {
            builder.add_custom_ignore_filename(f);
        }
        builder.hidden(true).git_ignore(true).overrides(overrides);
        builder.filter_entry(|entry| !entry.path().to_string_lossy().contains(CACHE_DIR));

        for entry in builder.build().flatten() {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();

                if PRESERVED_FILES.contains(&name_str.as_str()) {
                    continue;
                }

                if let Ok(mut file) = File::open(path) {
                    let mut buffer = [0; 1024];
                    let chunk_size = file.read(&mut buffer).unwrap_or(0);
                    if buffer[..chunk_size].contains(&0) {
                        continue;
                    }
                }

                if let Ok(content) = std::fs::read_to_string(path) {
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

#[pyfunction]
#[pyo3(signature = (file_path, query, prefix=None, case_sensitive=true))]
fn search_snap(
    file_path: String,
    query: String,
    prefix: Option<String>,
    case_sensitive: bool,
) -> PyResult<Vec<(String, usize, String)>> {
    let path = Path::new(&file_path);
    let prefix_str = prefix.unwrap_or_default();

    let files = load_snapshot_data(path, |p| p.starts_with(&prefix_str))
        .map_err(|e| PyIOError::new_err(format!("Failed to read snapshot: {}", e)))?;

    let mut results = Vec::new();
    let query_lower = if !case_sensitive {
        query.to_lowercase()
    } else {
        String::new()
    };

    for (name, content) in files {
        if content.contains(&0) {
            continue;
        }

        let text = String::from_utf8_lossy(&content);
        for (i, line) in text.lines().enumerate() {
            let is_match = if case_sensitive {
                line.contains(&query)
            } else {
                line.to_lowercase().contains(&query_lower)
            };

            if is_match {
                let display_line = if line.len() > 100 {
                    format!("{}...", &line[..100])
                } else {
                    line.to_string()
                };
                results.push((name.clone(), i + 1, display_line));
            }
        }
    }
    Ok(results)
}

#[pyfunction]
fn cat_file(file_path: String, target_file: String) -> PyResult<Vec<u8>> {
    let path = Path::new(&file_path);

    let files = load_snapshot_data(path, |p| p == target_file)
        .map_err(|e| PyIOError::new_err(format!("Failed to read snapshot: {}", e)))?;

    if let Some((_, content)) = files.into_iter().next() {
        Ok(content)
    } else {
        Err(PyValueError::new_err(format!(
            "File '{}' not found in snapshot",
            target_file
        )))
    }
}

// --- FIX 2: Add signature attribute to scan_locs_dir ---
#[pyfunction]
#[pyo3(signature = (source, exclude=None))]
fn scan_locs_dir(source: String, exclude: Option<Vec<String>>) -> PyResult<Vec<(String, usize)>> {
    let source_path = Path::new(&source);
    let mut results = Vec::new();

    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(excs) = exclude {
        for pattern in excs {
            let _ = override_builder.add(&format!("!{}", pattern));
        }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));
    let overrides = override_builder
        .build()
        .map_err(|e| PyIOError::new_err(e.to_string()))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES {
        builder.add_custom_ignore_filename(f);
    }
    builder.hidden(true).git_ignore(true).overrides(overrides);
    builder.filter_entry(|entry| !entry.path().to_string_lossy().contains(CACHE_DIR));

    for entry in builder.build().flatten() {
        let path = entry.path();
        if path.is_file() {
            let name = path.strip_prefix(source_path).unwrap_or(path);
            let name_str = name.to_string_lossy().to_string();
            if PRESERVED_FILES.contains(&name_str.as_str()) {
                continue;
            }

            let count = if let Ok(mut file) = File::open(path) {
                let mut buffer = [0; 1024];
                let chunk_size = file.read(&mut buffer).unwrap_or(0);
                if buffer[..chunk_size].contains(&0) {
                    0
                } else if file.seek(SeekFrom::Start(0)).is_ok() {
                    let reader = std::io::BufReader::new(file);
                    reader.lines().count()
                } else {
                    0
                }
            } else {
                0
            };
            results.push((name_str, count));
        }
    }
    Ok(results)
}

#[pyfunction]
fn list_files_details(file_path: String) -> PyResult<Vec<(String, u64)>> {
    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);
    let mut results = Vec::new();

    if let Ok(entries) = archive.entries() {
        for mut e in entries.flatten() {
            let size = e.size();
            let path = e.path().unwrap().into_owned();
            let path_str = path.to_string_lossy().to_string();

            if path_str == "manifest.json" {
                let mut content = String::new();
                if e.read_to_string(&mut content).is_ok()
                    && let Ok(manifest) = serde_json::from_str::<SnapshotManifest>(&content)
                {
                    return Ok(manifest
                        .entries
                        .into_iter()
                        .map(|en| (en.path, en.size))
                        .collect());
                }
            }

            if !path_str.starts_with("blobs/")
                && path_str != ".vegh.json"
                && path_str != "manifest.json"
            {
                results.push((path_str, size));
            }
        }
    }
    Ok(results)
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
    m.add_function(wrap_pyfunction!(scan_locs_dir, m)?)?;
    m.add_function(wrap_pyfunction!(cat_file, m)?)?;
    m.add_function(wrap_pyfunction!(list_files_details, m)?)?;
    m.add_function(wrap_pyfunction!(get_context_xml, m)?)?;
    m.add_function(wrap_pyfunction!(search_snap, m)?)?;
    m.add_function(wrap_pyfunction!(count_locs, m)?)?;
    m.add_function(wrap_pyfunction!(read_snapshot_text, m)?)?;
    Ok(())
}
