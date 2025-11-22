use pyo3::prelude::*;
use pyo3::exceptions::{PyIOError};
use std::fs::File;
use std::io::{Read};
use std::path::Path;
use ignore::WalkBuilder;
use sha2::{Digest, Sha256};

/// Create a snapshot (.snap) from a directory.
/// Returns the number of files compressed.
#[pyfunction]
fn create_snap(source: String, output: String) -> PyResult<usize> {
    let source_path = Path::new(&source);
    let output_path = Path::new(&output);

    let file = File::create(output_path)
        .map_err(|e| PyIOError::new_err(format!("Failed to create output file: {}", e)))?;

    // Zstd Encoder: Level 3 (Balanced)
    let encoder = zstd::stream::write::Encoder::new(file, 3)
        .map_err(|e| PyIOError::new_err(format!("Zstd init error: {}", e)))?;

    let mut tar = tar::Builder::new(encoder);
    let mut count = 0;

    let ignore_filename = ".veghignore";

    // 1. Manually add .veghignore if it exists
    let ignore_path = source_path.join(ignore_filename);
    if ignore_path.exists() && ignore_path.is_file() {
        let mut f = File::open(&ignore_path)
            .map_err(|e| PyIOError::new_err(format!("Failed to open .veghignore: {}", e)))?;
        
        tar.append_file(ignore_filename, &mut f)
            .map_err(|e| PyIOError::new_err(format!("Failed to archive .veghignore: {}", e)))?;
        count += 1;
    }

    // 2. Walk the directory respecting .veghignore
    let mut builder = WalkBuilder::new(source_path);
    builder.add_custom_ignore_filename(ignore_filename);
    builder.hidden(true);
    builder.git_ignore(true);

    let walker = builder.build();

    for result in walker {
        match result {
            Ok(entry) => {
                let path = entry.path();
                if path.is_file() {
                    let name = path.strip_prefix(source_path).unwrap_or(path);
                    let name_str = name.to_string_lossy();

                    if name_str == ignore_filename {
                        continue;
                    }

                    tar.append_path_with_name(path, name)
                        .map_err(|e| PyIOError::new_err(format!("Failed to archive {:?}: {}", path, e)))?;
                    
                    count += 1;
                }
            }
            Err(err) => {
                // In binding context, eprintln goes to stderr which Python can see
                eprintln!("Warning: Could not access file: {}", err);
            }
        }
    }

    let encoder = tar.into_inner()
        .map_err(|e| PyIOError::new_err(format!("Tar finalize error: {}", e)))?;
    encoder.finish()
        .map_err(|e| PyIOError::new_err(format!("Zstd finalize error: {}", e)))?;

    Ok(count)
}

/// Restore a snapshot (.snap) to a directory.
#[pyfunction]
fn restore_snap(file_path: String, out_dir: String) -> PyResult<()> {
    let input_path = Path::new(&file_path);
    let output_path = Path::new(&out_dir);

    let file = File::open(input_path)
        .map_err(|e| PyIOError::new_err(format!("Failed to open .snap file: {}", e)))?;

    let decoder = zstd::stream::read::Decoder::new(file)
        .map_err(|e| PyIOError::new_err(format!("Zstd decoder error: {}", e)))?;

    let mut archive = tar::Archive::new(decoder);
    
    archive.unpack(output_path)
        .map_err(|e| PyIOError::new_err(format!("Failed to unpack archive: {}", e)))?;

    Ok(())
}

/// Calculate SHA256 checksum of a file.
#[pyfunction]
fn check_integrity(file_path: String) -> PyResult<String> {
    let path = Path::new(&file_path);
    let mut file = File::open(path)
        .map_err(|e| PyIOError::new_err(format!("Failed to open file: {}", e)))?;

    let mut hasher = Sha256::new();
    let mut buffer = [0; 8192];

    loop {
        let count = file.read(&mut buffer)
            .map_err(|e| PyIOError::new_err(format!("Read error: {}", e)))?;
        if count == 0 { break; }
        hasher.update(&buffer[..count]);
    }

    let result = hasher.finalize();
    Ok(hex::encode(result))
}

/// The Python module definition.
#[pymodule]
#[pyo3(name = "_core")]
fn pyvegh_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(create_snap, m)?)?;
    m.add_function(wrap_pyfunction!(restore_snap, m)?)?;
    m.add_function(wrap_pyfunction!(check_integrity, m)?)?;
    Ok(())
}