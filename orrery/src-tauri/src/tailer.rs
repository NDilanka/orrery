//! Byte-offset incremental reader (PROTOCOL.md §1).
//!
//! Keeps the last read offset per path. On each `read_new`, seeks to the stored offset, reads
//! new bytes, splits on `\n`, and HOLDS a trailing partial (unterminated) line in a buffer until
//! its newline arrives.
//!
//! Rotation guard (R3): a delete+recreate to a SAME-OR-LARGER file used to desync the cursor —
//! the old size-shrink-only check never fires when the new file happens to be as big or bigger
//! than the old cursor offset, so the reader would keep tailing from the middle of a completely
//! different file. `read_new` now also compares the file's identity across calls: its creation
//! time when the platform reports one (the reliable signal — changes on a genuine recreate even
//! when the new file is bigger), OR the first ~64 bytes ("anchor") when it doesn't. Either
//! signal changing, OR the file having shrunk, resets the cursor to 0 and reports the rotation
//! back to the caller (`(lines, rotated)`) so it can rebuild anything derived from the old
//! contents (the watcher's live reducer — see `live.rs`).

use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

/// How many leading bytes of the file are fingerprinted as a rotation-detection anchor when the
/// platform/filesystem doesn't report a creation time.
const ANCHOR_LEN: usize = 64;

/// Per-path tail cursor: byte offset + a RAW byte buffer holding an unterminated trailing line,
/// plus the file-identity fingerprint (creation time / anchor bytes) captured at the last read.
/// The partial is bytes (not a `String`) so a multibyte UTF-8 char straddling a read boundary is
/// carried undecoded to the next read instead of being lossily decoded mid-sequence (which
/// silently corrupted the line — a `�` replacement char the watcher's serde parse then dropped).
#[derive(Debug, Default)]
struct Cursor {
    offset: u64,
    partial: Vec<u8>,
    created: Option<SystemTime>,
    anchor: Option<Vec<u8>>,
}

#[derive(Debug, Default)]
pub struct Tailer {
    cursors: HashMap<PathBuf, Cursor>,
}

/// Read up to `ANCHOR_LEN` leading bytes of an already-open file, WITHOUT disturbing the file's
/// seek position for the caller's subsequent read (the caller always re-seeks explicitly before
/// its own read, but this keeps the function honest regardless).
fn read_anchor(file: &mut File, len: u64) -> std::io::Result<Vec<u8>> {
    let n = (ANCHOR_LEN as u64).min(len) as usize;
    let mut buf = vec![0u8; n];
    file.seek(SeekFrom::Start(0))?;
    file.read_exact(&mut buf)?;
    Ok(buf)
}

impl Tailer {
    pub fn new() -> Self {
        Tailer {
            cursors: HashMap::new(),
        }
    }

    /// Seed a cursor for `path` at `offset` WITHOUT reading any lines — used when the caller has
    /// already consumed the file up to `offset` some other way (control.rs folds the whole
    /// historical log into a live reducer once on mount; seeding the tailer past that point means
    /// its first `read_new` sees zero "new" bytes instead of replaying + re-applying history a
    /// second time). Also snapshots the file's identity (creation time / anchor) at seed time so a
    /// rotation that happens between the seed and the first poll is still caught.
    pub fn seed<P: AsRef<Path>>(&mut self, path: P, offset: u64) {
        let mut cursor = Cursor {
            offset,
            ..Cursor::default()
        };
        if let Ok(mut f) = File::open(path.as_ref()) {
            if let Ok(meta) = f.metadata() {
                cursor.created = meta.created().ok();
                cursor.anchor = read_anchor(&mut f, meta.len()).ok();
            }
        }
        self.cursors.insert(path.as_ref().to_path_buf(), cursor);
    }

    /// Read complete lines appended since the last call. Returns owned `String`s with the
    /// trailing newline stripped (a trailing partial line is held until completed), and whether
    /// this call detected the file was ROTATED (deleted + recreated, or truncated in place) since
    /// the previous call — in which case the returned lines are the WHOLE new file from offset 0,
    /// not a diff against the old one.
    pub fn read_new<P: AsRef<Path>>(&mut self, path: P) -> std::io::Result<(Vec<String>, bool)> {
        let path = path.as_ref();
        let key = path.to_path_buf();
        let cursor = self.cursors.entry(key).or_default();

        let mut file = match File::open(path) {
            Ok(f) => f,
            // File not present yet — nothing new.
            Err(ref e) if e.kind() == std::io::ErrorKind::NotFound => return Ok((Vec::new(), false)),
            Err(e) => return Err(e),
        };

        let meta = file.metadata()?;
        let len = meta.len();
        let created = meta.created().ok();
        let anchor = read_anchor(&mut file, len)?;

        // Rotation check: only meaningful once we've actually read this file before (a fresh
        // cursor at offset 0 has nothing to compare against). Any of three independent signals
        // firing means "this is not the same file content we were tailing" — creation time
        // changing (the reliable signal when the platform reports one), the leading bytes
        // changing (catches a same-size-or-larger recreate when creation time is unavailable, or
        // even when it misleadingly matches), or the file having shrunk (a plain in-place
        // truncation, kept as a cheap unconditional safety net).
        //
        // The anchor comparison is over the OVERLAPPING prefix (`min(old_len, new_len)` bytes),
        // NOT a fixed 64 bytes — a growing file's anchor read keeps getting LONGER on every call
        // while it's still under ANCHOR_LEN, so comparing full buffers would flag every append to
        // a small, still-growing log as a false rotation. Plain appending never rewrites bytes
        // already on disk, so the shared prefix staying identical is exactly "not rotated";
        // a genuine recreate almost always differs somewhere in that same prefix.
        let mut rotated = false;
        if cursor.offset > 0 {
            let creation_changed = matches!((cursor.created, created), (Some(p), Some(n)) if p != n);
            let anchor_changed = match &cursor.anchor {
                Some(prev) => {
                    let n = prev.len().min(anchor.len());
                    n > 0 && prev[..n] != anchor[..n]
                }
                None => false,
            };
            let size_shrunk = len < cursor.offset;
            rotated = creation_changed || anchor_changed || size_shrunk;
        }
        if rotated {
            cursor.offset = 0;
            cursor.partial.clear();
        }
        cursor.created = created;
        cursor.anchor = Some(anchor);

        if len == cursor.offset {
            // No new bytes (possibly after a rotation reset to an empty new file).
            return Ok((Vec::new(), rotated));
        }

        file.seek(SeekFrom::Start(cursor.offset))?;
        let mut buf = Vec::with_capacity((len - cursor.offset) as usize);
        let read = file.read_to_end(&mut buf)?;
        cursor.offset += read as u64;

        // Split on b'\n' at the BYTE level: prepend any carried (incomplete) bytes from the last
        // read, then decode ONLY complete lines (lossily, per line — a genuinely malformed byte
        // inside a finished line still can't crash us). A trailing unterminated line — which may
        // end mid-multibyte-char — is carried RAW to the next read, so a UTF-8 sequence straddling
        // a read boundary is reassembled before it's ever decoded, instead of being lossily
        // decoded mid-sequence (which corrupted the line and the watcher then silently dropped it).
        let mut data = std::mem::take(&mut cursor.partial);
        data.extend_from_slice(&buf);

        let mut lines = Vec::new();
        let mut start = 0usize;
        for i in 0..data.len() {
            if data[i] == b'\n' {
                let mut line = &data[start..i];
                // strip a trailing '\r' (Windows CRLF tolerance) and skip blank lines
                if line.last() == Some(&b'\r') {
                    line = &line[..line.len() - 1];
                }
                if !line.is_empty() {
                    lines.push(String::from_utf8_lossy(line).into_owned());
                }
                start = i + 1;
            }
        }
        // Carry the remaining (unterminated) bytes — possibly a partial multibyte char — forward.
        cursor.partial = data[start..].to_vec();
        Ok((lines, rotated))
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
        append_bytes(path, s.as_bytes());
    }

    fn append_bytes(path: &Path, bytes: &[u8]) {
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap();
        f.write_all(bytes).unwrap();
        f.flush().unwrap();
    }

    #[test]
    fn multibyte_char_split_across_reads_decodes_intact() {
        // "café\n" — the é is 0xC3 0xA9 in UTF-8. Append the line in two halves that split é down
        // the MIDDLE (first read ends on 0xC3, second starts on 0xA9), simulating a write flushed
        // between two `read_new` polls. The old per-read `from_utf8_lossy` turned the dangling
        // 0xC3 into a replacement char and advanced past it, corrupting the line; the raw carry
        // buffer must instead reassemble the char and decode the line intact.
        let path = tmp_path("multibyte_split");
        let mut t = Tailer::new();

        let full = "café\n".as_bytes().to_vec(); // c a f 0xC3 0xA9 \n  (6 bytes)
        assert_eq!(full.len(), 6);

        // First read sees only "caf" + the leading byte of é (0xC3) — no complete line yet.
        append_bytes(&path, &full[..4]);
        let (l1, r1) = t.read_new(&path).unwrap();
        assert!(l1.is_empty(), "no complete line while the char is still split: {l1:?}");
        assert!(!r1);

        // Second read supplies the trailing byte of é (0xA9) + the newline → the line completes.
        append_bytes(&path, &full[4..]);
        let (l2, r2) = t.read_new(&path).unwrap();
        assert_eq!(l2, vec!["café".to_string()], "the split multibyte char must decode intact");
        assert!(!r2);

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn holds_partial_line_across_appends() {
        let path = tmp_path("partial");
        let mut t = Tailer::new();

        // First append: one full line + a partial (no trailing newline).
        append(&path, "{\"event\":\"a\"}\n{\"event\":\"b\"");
        let (lines1, r1) = t.read_new(&path).unwrap();
        assert_eq!(lines1, vec!["{\"event\":\"a\"}".to_string()]);
        assert!(!r1);

        // Second append completes the partial line and adds another full one.
        append(&path, "}\n{\"event\":\"c\"}\n");
        let (lines2, r2) = t.read_new(&path).unwrap();
        assert_eq!(
            lines2,
            vec!["{\"event\":\"b\"}".to_string(), "{\"event\":\"c\"}".to_string()]
        );
        assert!(!r2);

        // No new bytes → empty.
        let (lines3, r3) = t.read_new(&path).unwrap();
        assert!(lines3.is_empty());
        assert!(!r3);

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn truncation_resets_cursor_and_is_reported_as_rotated() {
        let path = tmp_path("trunc");
        let mut t = Tailer::new();
        append(&path, "one\ntwo\n");
        let (l, r0) = t.read_new(&path).unwrap();
        assert_eq!(l, vec!["one".to_string(), "two".to_string()]);
        assert!(!r0);

        // truncate the file to something SHORTER than the cursor offset (8 bytes)
        std::fs::write(&path, "fresh\n").unwrap();
        let (l2, r2) = t.read_new(&path).unwrap();
        assert_eq!(l2, vec!["fresh".to_string()]);
        assert!(r2, "a shrink must be reported as a rotation");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn delete_recreate_larger_is_detected_as_rotation() {
        // The bug R3 fixes: a delete+recreate to a SAME-OR-LARGER file previously desynced the
        // cursor entirely (the old size-shrink-only guard never fires here), so the reader would
        // keep tailing from the middle of a totally different file's byte stream.
        let path = tmp_path("rotate_larger");
        let mut t = Tailer::new();
        append(&path, "aaa\nbbb\n"); // 8 bytes
        let (l1, r1) = t.read_new(&path).unwrap();
        assert_eq!(l1, vec!["aaa".to_string(), "bbb".to_string()]);
        assert!(!r1);

        // delete + recreate at the SAME path with DIFFERENT, LARGER content.
        std::fs::remove_file(&path).unwrap();
        append(&path, "zzz\nyyy\nxxx\nwww\n"); // 16 bytes > the old 8-byte cursor offset

        let (l2, r2) = t.read_new(&path).unwrap();
        assert!(r2, "delete+recreate-larger must be detected as a rotation");
        // rotation resets the cursor to 0, so this returns the WHOLE new file, not a byte-offset
        // continuation of the old one (which would have produced garbage or missed lines).
        assert_eq!(
            l2,
            vec!["zzz".to_string(), "yyy".to_string(), "xxx".to_string(), "www".to_string()]
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn seed_skips_already_consumed_bytes_and_still_detects_a_later_rotation() {
        let path = tmp_path("seed");
        append(&path, "one\ntwo\n"); // 8 bytes — imagine control.rs already folded these into a
                                     // live reducer via `build_live` before the tailer existed.
        let len = std::fs::metadata(&path).unwrap().len();

        let mut t = Tailer::new();
        t.seed(&path, len);
        // nothing new yet — the seed already accounts for "one\ntwo\n".
        let (l0, r0) = t.read_new(&path).unwrap();
        assert!(l0.is_empty(), "seeded offset must not re-read history: {l0:?}");
        assert!(!r0);

        append(&path, "three\n");
        let (l1, r1) = t.read_new(&path).unwrap();
        assert_eq!(l1, vec!["three".to_string()]);
        assert!(!r1);

        // a rotation AFTER seeding is still caught.
        std::fs::remove_file(&path).unwrap();
        append(&path, "fresh-one\nfresh-two\nfresh-three\n");
        let (l2, r2) = t.read_new(&path).unwrap();
        assert!(r2);
        assert_eq!(
            l2,
            vec!["fresh-one".to_string(), "fresh-two".to_string(), "fresh-three".to_string()]
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn missing_file_is_empty() {
        let path = tmp_path("missing_never_created");
        let mut t = Tailer::new();
        let (l, r) = t.read_new(&path).unwrap();
        assert!(l.is_empty());
        assert!(!r);
    }
}
