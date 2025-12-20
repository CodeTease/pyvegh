use pyo3::prelude::*;
use pyo3::exceptions::{PyIOError, PyValueError};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, BufReader, BufRead, Seek, SeekFrom}; // Added Seek traits
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use ignore::{WalkBuilder, overrides::OverrideBuilder};
use blake3::Hasher; 
use memmap2::MmapOptions; 
use serde::{Serialize, Deserialize};
use chrono::Utc;

const PRESERVED_FILES: &[&str] = &[".veghignore", ".gitignore", ".dockerignore", ".npmignore"];
const CACHE_DIR: &str = ".veghcache";
const CACHE_FILE: &str = "index.json";
const SNAPSHOT_FORMAT_VERSION: &str = "2"; 
const VEGH_COMPAT_VERSION: &str = "0.3.0";

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

#[pyfunction]
#[pyo3(signature = (source, output, level=3, comment=None, include=None, exclude=None, no_cache=false))]
fn create_snap(
    source: String, 
    output: String, 
    level: i32, 
    comment: Option<String>,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>,
    no_cache: bool 
) -> PyResult<usize> {
    let source_path = Path::new(&source);
    let output_path = Path::new(&output);
    let file = File::create(output_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    
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
    
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    encoder.multithread(workers as u32).map_err(|e| PyIOError::new_err(format!("Zstd MT error: {}", e)))?;

    let mut tar = tar::Builder::new(encoder);

    let mut header = tar::Header::new_gnu();
    header.set_path(".vegh.json").unwrap();
    header.set_size(meta_json.len() as u64);
    header.set_mode(0o644);
    header.set_cksum();
    tar.append_data(&mut header, ".vegh.json", meta_json.as_bytes())
        .map_err(|e| PyIOError::new_err(e.to_string()))?;

    let mut count = 0;
    
    for &name in PRESERVED_FILES {
        let p = source_path.join(name);
        if p.exists() {
            let mut f = File::open(&p).map_err(|e| PyIOError::new_err(e.to_string()))?;
            tar.append_file(name, &mut f).map_err(|e| PyIOError::new_err(e.to_string()))?;
            count += 1;
        }
    }

    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(incs) = include {
        for pattern in incs { let _ = override_builder.add(&format!("!{}", pattern)); }
    }
    if let Some(excs) = exclude {
        for pattern in excs { let _ = override_builder.add(&pattern); }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));

    let overrides = override_builder.build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES { builder.add_custom_ignore_filename(f); }
    
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                if let Ok(abs) = fs::canonicalize(path) { 
                    if abs == output_abs { continue; } 
                }

                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();
                if PRESERVED_FILES.contains(&name_str.as_str()) { continue; }

                let metadata = path.metadata().map_err(|e| PyIOError::new_err(e.to_string()))?;
                let modified = metadata.modified()
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
    enc.finish().map_err(|e| PyIOError::new_err(format!("Finalize error: {}", e)))?;

    Ok(count)
}

#[pyfunction]
#[pyo3(signature = (source, include=None, exclude=None))]
fn dry_run_snap(
    source: String, 
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>
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

    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(incs) = include {
        for pattern in incs { let _ = override_builder.add(&format!("!{}", pattern)); }
    }
    if let Some(excs) = exclude {
        for pattern in excs { let _ = override_builder.add(&pattern); }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));
    
    let overrides = override_builder.build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES { builder.add_custom_ignore_filename(f); }
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();
                if PRESERVED_FILES.contains(&name_str.as_str()) { continue; }
                let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
                results.push((name_str, size));
            }
        }
    }

    Ok(results)
}


#[pyfunction]
#[pyo3(signature = (file_path, out_dir, include=None))]
fn restore_snap(
    file_path: String, 
    out_dir: String,
    include: Option<Vec<String>>
) -> PyResult<()> {
    let out = Path::new(&out_dir);
    if !out.exists() { fs::create_dir_all(out).map_err(|e| PyIOError::new_err(e.to_string()))?; }

    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    for entry in archive.entries().map_err(|e| PyIOError::new_err(e.to_string()))? {
        let mut entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        let path = entry.path().unwrap().into_owned();
        if path.to_string_lossy() == ".vegh.json" { continue; }

        if let Some(ref incs) = include {
             let mut matched = false;
             for pattern in incs {
                 if path.starts_with(Path::new(pattern)) {
                     matched = true;
                     break;
                 }
             }
             if !matched { continue; }
        }

        entry.unpack_in(out).map_err(|e| PyIOError::new_err(e.to_string()))?;
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
                if let Ok(p) = e.path() { files.push(p.to_string_lossy().to_string()); }
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
                        e.read_to_string(&mut content).map_err(|e| PyIOError::new_err(e.to_string()))?;
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
                
                if path_str == ".vegh.json" { continue; }
                let mut content = String::new();
                match e.read_to_string(&mut content) {
                    Ok(_) => {
                        if content.contains('\0') {
                            results.push((path_str, 0));
                        } else {
                            results.push((path_str, content.lines().count()));
                        }
                    },
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
fn scan_locs_dir(
    source: String,
    exclude: Option<Vec<String>>
) -> PyResult<Vec<(String, usize)>> {
    let source_path = Path::new(&source);
    let mut results = Vec::new();

    let mut override_builder = OverrideBuilder::new(source_path);
    if let Some(excs) = exclude {
        for pattern in excs { let _ = override_builder.add(&pattern); }
    }
    let _ = override_builder.add(&format!("!{}", CACHE_DIR));
    
    let overrides = override_builder.build()
        .map_err(|e| PyIOError::new_err(format!("Override build fail: {}", e)))?;

    let mut builder = WalkBuilder::new(source_path);
    for &f in PRESERVED_FILES { builder.add_custom_ignore_filename(f); }
    builder.hidden(true).git_ignore(true).overrides(overrides);

    for res in builder.build() {
        if let Ok(entry) = res {
            let path = entry.path();
            if path.is_file() {
                let name = path.strip_prefix(source_path).unwrap_or(path);
                let name_str = name.to_string_lossy().to_string();
                if PRESERVED_FILES.contains(&name_str.as_str()) { continue; }

                // LOGIC FIX: Đếm dòng chính xác bằng BufReader.lines()
                let count = if let Ok(mut file) = File::open(path) {
                    // Check binary (Header check)
                    let mut buffer = [0; 1024];
                    let chunk_size = file.read(&mut buffer).unwrap_or(0);
                    
                    if buffer[..chunk_size].contains(&0) {
                        0
                    } else {
                        // REWIND lại đầu file để đếm từ đầu
                        if file.seek(SeekFrom::Start(0)).is_ok() {
                            let reader = BufReader::new(file);
                            // lines().count() xử lý đúng cả trường hợp không có \n ở cuối
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

    for entry in archive.entries().map_err(|e| PyIOError::new_err(e.to_string()))? {
        let mut entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        let path = entry.path().unwrap().into_owned();
        let path_str = path.to_string_lossy().to_string();

        if path_str == target_file {
            let mut content = Vec::new();
            entry.read_to_end(&mut content).map_err(|e| PyIOError::new_err(e.to_string()))?;
            return Ok(content);
        }
    }
    Err(PyValueError::new_err(format!("File '{}' not found in snapshot", target_file)))
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
    Ok(())
}