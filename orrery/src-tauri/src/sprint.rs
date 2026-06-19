//! Parse `sprint-status.yaml` (BMAD) into item & group statuses (PROTOCOL.md §4 rule 4).
//!
//! The `development_status` map mixes three kinds of keys:
//!   - `epic-N`                  → group (epic) lifecycle: backlog | in-progress | done
//!   - `epic-N-retrospective`    → group retro status: optional | done | pending
//!   - `N-M-...` (story key)      → work item status: backlog | ready-for-dev | in-progress | review | done
//!
//! Tolerant of absence: returns empty vectors when the file/section is missing.

use crate::model::{GroupStatus, ItemStatus, RetroStatus};
use serde_yaml::Value;

/// (story key, status, epic id)
pub type ItemStatusRow = (String, ItemStatus, Option<String>);
/// (epic id, group status, retro status)
pub type GroupStatusRow = (String, GroupStatus, Option<RetroStatus>);

pub struct SprintStatus {
    pub items: Vec<ItemStatusRow>,
    pub groups: Vec<GroupStatusRow>,
}

impl SprintStatus {
    pub fn empty() -> Self {
        SprintStatus {
            items: Vec::new(),
            groups: Vec::new(),
        }
    }
}

/// Parse YAML text. Never panics: malformed input → empty.
pub fn parse_str(text: &str) -> SprintStatus {
    let root: Value = match serde_yaml::from_str(text) {
        Ok(v) => v,
        Err(_) => return SprintStatus::empty(),
    };

    let dev = match root.get("development_status") {
        Some(Value::Mapping(m)) => m,
        _ => return SprintStatus::empty(),
    };

    let mut items: Vec<ItemStatusRow> = Vec::new();
    let mut retro_by_epic: std::collections::HashMap<String, RetroStatus> =
        std::collections::HashMap::new();
    let mut epic_status: Vec<(String, GroupStatus)> = Vec::new();

    for (k, v) in dev {
        let key = match k.as_str() {
            Some(s) => s.to_string(),
            None => continue,
        };
        let val = match v.as_str() {
            Some(s) => s.to_string(),
            None => continue,
        };

        if let Some(rest) = key.strip_prefix("epic-") {
            if let Some(epic_num) = rest.strip_suffix("-retrospective") {
                if let Some(rs) = parse_retro(&val) {
                    retro_by_epic.insert(epic_num.to_string(), rs);
                }
            } else if rest.chars().all(|c| c.is_ascii_digit()) && !rest.is_empty() {
                // plain epic-N
                if let Some(gs) = parse_group(&val) {
                    epic_status.push((rest.to_string(), gs));
                }
            }
            continue;
        }

        // story key: starts with digit-dash
        if is_story_key(&key) {
            if let Some(st) = parse_item(&val) {
                let epic = epic_of(&key);
                items.push((key, st, epic));
            }
        }
    }

    let mut groups: Vec<GroupStatusRow> = Vec::new();
    for (epic, gs) in epic_status {
        let retro = retro_by_epic.remove(&epic);
        groups.push((epic, gs, retro));
    }
    // any retro whose epic-N line was absent still surfaces as a pending/optional group
    for (epic, rs) in retro_by_epic {
        groups.push((epic, GroupStatus::Backlog, Some(rs)));
    }

    SprintStatus { items, groups }
}

/// Parse a file path; tolerant of absence (returns empty).
pub fn parse_file<P: AsRef<std::path::Path>>(path: P) -> SprintStatus {
    match std::fs::read_to_string(path) {
        Ok(text) => parse_str(&text),
        Err(_) => SprintStatus::empty(),
    }
}

fn is_story_key(k: &str) -> bool {
    let mut parts = k.split('-');
    matches!(parts.next(), Some(h) if !h.is_empty() && h.chars().all(|c| c.is_ascii_digit()))
        && matches!(parts.next(), Some(s) if !s.is_empty() && s.chars().all(|c| c.is_ascii_digit()))
}

fn epic_of(story: &str) -> Option<String> {
    story.split('-').next().and_then(|h| {
        if !h.is_empty() && h.chars().all(|c| c.is_ascii_digit()) {
            Some(h.to_string())
        } else {
            None
        }
    })
}

fn parse_item(s: &str) -> Option<ItemStatus> {
    Some(match s {
        "backlog" => ItemStatus::Backlog,
        "ready" | "ready-for-dev" => ItemStatus::Ready,
        "in-progress" => ItemStatus::InProgress,
        "review" => ItemStatus::Review,
        "done" => ItemStatus::Done,
        "blocked" => ItemStatus::Blocked,
        "failed" => ItemStatus::Failed,
        _ => return None,
    })
}

fn parse_group(s: &str) -> Option<GroupStatus> {
    Some(match s {
        "backlog" => GroupStatus::Backlog,
        "in-progress" => GroupStatus::InProgress,
        "done" => GroupStatus::Done,
        _ => return None,
    })
}

fn parse_retro(s: &str) -> Option<RetroStatus> {
    Some(match s {
        "optional" => RetroStatus::Optional,
        "done" => RetroStatus::Done,
        "pending" => RetroStatus::Pending,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const SPRINT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../fixtures/sprint-status.yaml");

    #[test]
    fn parses_fixture() {
        let s = parse_file(SPRINT);
        // 7 epics in the fixture
        assert!(s.groups.len() >= 7, "expected >=7 groups, got {}", s.groups.len());
        // many stories
        assert!(s.items.len() > 20, "expected many stories, got {}", s.items.len());

        // 3-4 is done
        let s34 = s.items.iter().find(|(k, _, _)| k == "3-4-semantic-search");
        assert_eq!(s34.map(|x| x.1), Some(ItemStatus::Done));

        // 3-5 is backlog
        let s35 = s.items.iter().find(|(k, _, _)| k == "3-5-re-embedding-batch-utility");
        assert_eq!(s35.map(|x| x.1), Some(ItemStatus::Backlog));

        // epic-1 done? it's in-progress in the fixture; epic-3 in-progress
        let e3 = s.groups.iter().find(|(id, _, _)| id == "3");
        assert_eq!(e3.map(|x| x.1), Some(GroupStatus::InProgress));
        // epic-3 retro optional
        assert_eq!(e3.and_then(|x| x.2), Some(RetroStatus::Optional));

        // epic-1 retro done
        let e1 = s.groups.iter().find(|(id, _, _)| id == "1");
        assert_eq!(e1.and_then(|x| x.2), Some(RetroStatus::Done));
    }

    #[test]
    fn missing_file_is_empty() {
        let s = parse_file("D:/this/does/not/exist.yaml");
        assert!(s.items.is_empty());
        assert!(s.groups.is_empty());
    }

    #[test]
    fn malformed_is_empty() {
        let s = parse_str("not: : valid: : yaml: [[[");
        assert!(s.items.is_empty());
    }
}
