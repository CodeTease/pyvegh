use pyo3::prelude::*;
use pyo3::exceptions::{PyIOError, PyValueError};
use std::fs::{self, File};
use std::io::Read;
use std::path::Path;
use ignore::{WalkBuilder, overrides::OverrideBuilder};
use sha2::{Digest, Sha256};
use serde::{Serialize, Deserialize};
use chrono::Utc;

const PRESERVED_FILES: &[&str] = &[".veghignore", ".gitignore"];

#[derive(Serialize, Deserialize)]
struct VeghMetadata {
    author: String,
    timestamp: i64,
    comment: String,
    tool_version: String,
}

#[pyfunction]
#[pyo3(signature = (source, output, level=3, comment=None, include=None, exclude=None))]
fn create_snap(
    source: String, 
    output: String, 
    level: i32, 
    comment: Option<String>,
    include: Option<Vec<String>>,
    exclude: Option<Vec<String>>
) -> PyResult<usize> {
    let source_path = Path::new(&source);
    let output_path = Path::new(&output);
    let file = File::create(output_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    
    let output_abs = fs::canonicalize(output_path).unwrap_or_else(|_| output_path.to_path_buf());

    let meta = VeghMetadata {
        author: "CodeTease (PyVegh)".to_string(),
        timestamp: Utc::now().timestamp(),
        comment: comment.unwrap_or_default(),
        tool_version: "PyVegh 0.2.0".to_string(),
    };
    let meta_json = serde_json::to_string_pretty(&meta).unwrap();

    let encoder = zstd::stream::write::Encoder::new(file, level)
        .map_err(|e| PyIOError::new_err(e.to_string()))?;
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
        for pattern in incs {
            let _ = override_builder.add(&format!("!{}", pattern)); 
        }
    }
    if let Some(excs) = exclude {
        for pattern in excs {
            let _ = override_builder.add(&pattern); 
        }
    }
    
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
                if PRESERVED_FILES.contains(&name.to_string_lossy().as_ref()) { continue; }
                
                tar.append_path_with_name(path, name)
                    .map_err(|e| PyIOError::new_err(e.to_string()))?;
                count += 1;
            }
        }
    }

    let enc = tar.into_inner().unwrap();
    enc.finish().map_err(|e| PyIOError::new_err(format!("Finalize error: {}", e)))?;

    Ok(count)
}

#[pyfunction]
#[pyo3(signature = (file_path, out_dir))]
fn restore_snap(file_path: String, out_dir: String) -> PyResult<()> {
    let out = Path::new(&out_dir);
    if !out.exists() { fs::create_dir_all(out).map_err(|e| PyIOError::new_err(e.to_string()))?; }

    let file = File::open(&file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let decoder = zstd::stream::read::Decoder::new(file).unwrap();
    let mut archive = tar::Archive::new(decoder);

    for entry in archive.entries().map_err(|e| PyIOError::new_err(e.to_string()))? {
        let mut entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        let path = entry.path().unwrap().into_owned();
        if path.to_string_lossy() == ".vegh.json" { continue; }
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
    let mut f = File::open(file_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let mut sha = Sha256::new();
    std::io::copy(&mut f, &mut sha).map_err(|e| PyIOError::new_err(e.to_string()))?;
    Ok(hex::encode(sha.finalize()))
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

#[pymodule]
#[pyo3(name = "_core")]
fn pyvegh_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(create_snap, m)?)?;
    m.add_function(wrap_pyfunction!(restore_snap, m)?)?;
    m.add_function(wrap_pyfunction!(list_files, m)?)?;
    m.add_function(wrap_pyfunction!(check_integrity, m)?)?;
    m.add_function(wrap_pyfunction!(get_metadata, m)?)?; 
    Ok(())
}