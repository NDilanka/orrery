//! Byte-offset incremental reader (PROTOCOL.md §1).
//!
//! Keeps the last read offset per path. On each `read_new`, seeks to the stored offset, reads
//! new bytes, splits on `\n`, and HOLDS a trailing partial (unterminated) line in a buffer until
//! its newline arrives. Truncation guard: if the file is shorter than the stored offset, reset
//! to 0 and start over.

use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// Per-path tail cursor: byte offset + a buffer holding an unterminated trailing line.
#[derive(Debug, Default)]
struct Cursor {
    offset: u64,
    partial: String,
}

#[derive(Debug, Default)]
pub struct Tailer {
    cursors: HashMap<PathBuf, Cursor>,
}

impl Tailer {
    pub fn new() -> Self {
        Tailer {
            cursors: HashMap::new(),
        }
    }

    /// Read complete lines appended since the last call. Returns owned `String`s with the
    /// trailing newline stripped. A trailing partial line is held until completed.
    pub fn read_new<P: AsRef<Path>>(&mut self, path: P) -> std::io::Result<Vec<String>> {
        let path = path.as_ref();
        let key = path.to_path_buf();
        let cursor = self.cursors.entry(key).or_default();

        let mut file = match File::open(path) {
            Ok(f) => f,
            // File not present yet — nothing new.
            Err(ref e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(e) => return Err(e),
        };

        let len = file.metadata()?.len();

        // Truncation guard: file shrank → reset cursor.
        if len < cursor.offset {
            cursor.offset = 0;
            cursor.partial.clear();
        }

        if len == cursor.offset {
            // No new bytes.
            return Ok(Vec::new());
        }

        file.seek(SeekFrom::Start(cursor.offset))?;
        let mut buf = Vec::with_capacity((len - cursor.offset) as usize);
        let read = file.read_to_end(&mut buf)?;
        cursor.offset += read as u64;

        // Decode lossily so a half-written multibyte sequence at the boundary doesn't crash us;
        // the bytes themselves stay accounted for by the offset, but a truly partial UTF-8 char
        // at the very end is rare in JSONL (newline-terminated ASCII-ish). We append decoded text
        // to the held partial and split on '\n'.
        let chunk = String::from_utf8_lossy(&buf);
        cursor.partial.push_str(&chunk);

        let mut lines = Vec::new();
        // Split, keeping the last element (after the final '\n', possibly empty) as the new partial.
        let ends_with_nl = cursor.partial.ends_with('\n');
        let mut parts: Vec<&str> = cursor.partial.split('\n').collect();

        // If the buffer ends with '\n', the last part is "" and there's no held partial.
        // Otherwise the last part is the unterminated line to hold.
        let held = if ends_with_nl {
            parts.pop(); // drop the trailing empty
            String::new()
        } else {
            parts.pop().unwrap_or("").to_string()
        };

        for p in parts {
            // strip a trailing '\r' (Windows CRLF tolerance) and skip blank lines
            let line = p.strip_suffix('\r').unwrap_or(p);
            if !line.is_empty() {
                lines.push(line.to_string());
            }
        }

        cursor.partial = held;
        Ok(lines)
    }

    /// Reset the cursor for a path (e.g. to re-read from the start).
    pub fn reset<P: AsRef<Path>>(&mut self, path: P) {
        self.cursors.remove(path.as_ref());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn tmp_path(name: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!("orrery_tailer_{}_{}", std::process::id(), name));
        let _ = std::fs::remove_file(&p);
        p
    }

    fn append(path: &Path, s: &str) {
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap();
        f.write_all(s.as_bytes()).unwrap();
        f.flush().unwrap();
    }

    #[test]
    fn holds_partial_line_across_appends() {
        let path = tmp_path("partial");
        let mut t = Tailer::new();

        // First append: one full line + a partial (no trailing newline).
        append(&path, "{\"event\":\"a\"}\n{\"event\":\"b\"");
        let lines1 = t.read_new(&path).unwrap();
        assert_eq!(lines1, vec!["{\"event\":\"a\"}".to_string()]);

        // Second append completes the partial line and adds another full one.
        append(&path, "}\n{\"event\":\"c\"}\n");
        let lines2 = t.read_new(&path).unwrap();
        assert_eq!(
            lines2,
            vec!["{\"event\":\"b\"}".to_string(), "{\"event\":\"c\"}".to_string()]
        );

        // No new bytes → empty.
        let lines3 = t.read_new(&path).unwrap();
        assert!(lines3.is_empty());

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn truncation_resets_cursor() {
        let path = tmp_path("trunc");
        let mut t = Tailer::new();
        append(&path, "one\ntwo\n");
        let l = t.read_new(&path).unwrap();
        assert_eq!(l, vec!["one".to_string(), "two".to_string()]);

        // truncate the file
        std::fs::write(&path, "fresh\n").unwrap();
        let l2 = t.read_new(&path).unwrap();
        assert_eq!(l2, vec!["fresh".to_string()]);

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn missing_file_is_empty() {
        let path = tmp_path("missing_never_created");
        let mut t = Tailer::new();
        let l = t.read_new(&path).unwrap();
        assert!(l.is_empty());
    }
}
