// Cartograph Rust contamination scanner.
//
// std-only (no external crates - mirrors the zero-dependency policy and lets
// the engine compile it with a bare `rustc`). Reads source file paths from
// argv, lexes each with awareness of // line comments, /* */ nested block
// comments, normal/byte strings, and raw strings (r"...", r#"..."#, br#...),
// then reports contamination findings as a JSON array on stdout:
//
//   [{"kind":"print","file":"src/lib.rs","line":12,
//     "detail":"println! in src/","severity":"block"}, ...]
//
// Contract (shared across every Cartograph engine): contamination blocks in
// src/ and warns in tests/examples. Code-shaped checks run against a
// "sanitized" view of each line where comment and string spans are blanked to
// spaces, so a match can never come from inside a comment or string. Content
// checks (urls, ips, abs paths, credentials) run against captured string
// literals. The declared dependency list is read from widget.json in the
// working directory for the unlisted-import check, exactly like go_scanner.

use std::collections::HashSet;
use std::env;
use std::fs;
use std::io::Write;

struct Finding {
    kind: String,
    file: String,
    line: usize,
    detail: String,
    severity: String,
}

struct Lexed {
    // One sanitized string per source line (comments/strings -> spaces).
    sani: Vec<String>,
    // (line_number_1_based, literal_contents) for every string literal.
    literals: Vec<(usize, String)>,
    // Line numbers (1-based) that are doc comments (/// or //!).
    doc_lines: Vec<usize>,
}

fn is_ident(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

fn lex(src: &str) -> Lexed {
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut sani: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut literals: Vec<(usize, String)> = Vec::new();
    let mut doc_lines: Vec<usize> = Vec::new();
    let mut line: usize = 1;

    // Push a char to the current sanitized line, advancing lines on newline.
    macro_rules! emit {
        ($c:expr) => {{
            let c = $c;
            if c == '\n' {
                sani.push(std::mem::take(&mut cur));
                line += 1;
            } else {
                cur.push(c);
            }
        }};
    }

    let mut i = 0usize;
    while i < n {
        let c = chars[i];
        let prev = if i > 0 { chars[i - 1] } else { ' ' };

        // line / block comments
        if c == '/' && i + 1 < n && chars[i + 1] == '/' {
            let third = if i + 2 < n { chars[i + 2] } else { ' ' };
            if third == '/' || third == '!' {
                doc_lines.push(line);
            }
            while i < n && chars[i] != '\n' {
                emit!(' ');
                i += 1;
            }
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '*' {
            let mut depth = 1;
            emit!(' ');
            emit!(' ');
            i += 2;
            while i < n && depth > 0 {
                if chars[i] == '/' && i + 1 < n && chars[i + 1] == '*' {
                    depth += 1;
                    emit!(' ');
                    emit!(' ');
                    i += 2;
                } else if chars[i] == '*' && i + 1 < n && chars[i + 1] == '/' {
                    depth -= 1;
                    emit!(' ');
                    emit!(' ');
                    i += 2;
                } else {
                    emit!(if chars[i] == '\n' { '\n' } else { ' ' });
                    i += 1;
                }
            }
            continue;
        }

        // raw / byte strings: r"...", r#"..."#, b"...", br"...", br#"..."#
        if (c == 'r' || c == 'b') && !is_ident(prev) {
            if let Some((consumed, lit, start_line)) =
                try_raw_or_byte_string(&chars, i, line)
            {
                literals.push((start_line, lit));
                for _ in 0..consumed {
                    emit!(if chars[i] == '\n' { '\n' } else { ' ' });
                    i += 1;
                }
                continue;
            }
        }

        // normal string
        if c == '"' {
            let start_line = line;
            let mut lit = String::new();
            emit!(' ');
            i += 1;
            while i < n {
                let d = chars[i];
                if d == '\\' && i + 1 < n {
                    lit.push(d);
                    lit.push(chars[i + 1]);
                    emit!(' ');
                    emit!(if chars[i + 1] == '\n' { '\n' } else { ' ' });
                    i += 2;
                    continue;
                }
                if d == '"' {
                    emit!(' ');
                    i += 1;
                    break;
                }
                lit.push(d);
                emit!(if d == '\n' { '\n' } else { ' ' });
                i += 1;
            }
            literals.push((start_line, lit));
            continue;
        }

        // char literal vs lifetime
        if c == '\'' {
            if i + 1 < n && chars[i + 1] == '\\' {
                emit!(' ');
                i += 1;
                let mut k = 0;
                while i < n && chars[i] != '\'' && k < 8 {
                    emit!(' ');
                    i += 1;
                    k += 1;
                }
                if i < n && chars[i] == '\'' {
                    emit!(' ');
                    i += 1;
                }
                continue;
            }
            if i + 2 < n && chars[i + 2] == '\'' {
                emit!(' ');
                emit!(' ');
                emit!(' ');
                i += 3;
                continue;
            }
            emit!(c);
            i += 1;
            continue;
        }

        emit!(c);
        i += 1;
    }
    sani.push(cur);

    Lexed { sani, literals, doc_lines }
}

fn try_raw_or_byte_string(
    chars: &[char],
    start: usize,
    line: usize,
) -> Option<(usize, String, usize)> {
    let n = chars.len();
    let mut i = start;
    let mut raw = false;
    if chars[i] == 'b' {
        i += 1;
        if i >= n {
            return None;
        }
    }
    if i < n && chars[i] == 'r' {
        raw = true;
        i += 1;
    }
    if raw {
        let mut hashes = 0;
        while i < n && chars[i] == '#' {
            hashes += 1;
            i += 1;
        }
        if i >= n || chars[i] != '"' {
            return None;
        }
        i += 1;
        let body_start = i;
        loop {
            if i >= n {
                break;
            }
            if chars[i] == '"' {
                let mut k = 0;
                while i + 1 + k < n && k < hashes && chars[i + 1 + k] == '#' {
                    k += 1;
                }
                if k == hashes {
                    let body: String = chars[body_start..i].iter().collect();
                    let consumed = (i + 1 + hashes) - start;
                    return Some((consumed, body, line));
                }
            }
            i += 1;
        }
        let body: String = chars[body_start..n.min(i)].iter().collect();
        return Some((i - start, body, line));
    }
    if i < n && chars[i] == '"' {
        i += 1;
        let body_start = i;
        while i < n {
            if chars[i] == '\\' && i + 1 < n {
                i += 2;
                continue;
            }
            if chars[i] == '"' {
                let body: String = chars[body_start..i].iter().collect();
                return Some((i + 1 - start, body, line));
            }
            i += 1;
        }
        let body: String = chars[body_start..n.min(i)].iter().collect();
        return Some((i - start, body, line));
    }
    None
}

fn contains_word(hay: &str, needle: &str) -> bool {
    let hb = hay.as_bytes();
    let nb = needle.as_bytes();
    if nb.is_empty() || nb.len() > hb.len() {
        return false;
    }
    let mut i = 0;
    while i + nb.len() <= hb.len() {
        if &hb[i..i + nb.len()] == nb {
            let before = if i == 0 { b' ' } else { hb[i - 1] };
            let after = if i + nb.len() < hb.len() {
                hb[i + nb.len()]
            } else {
                b' '
            };
            let ok_before = !(before.is_ascii_alphanumeric() || before == b'_');
            let ok_after = !(after.is_ascii_alphanumeric() || after == b'_');
            if ok_before && ok_after {
                return true;
            }
        }
        i += 1;
    }
    false
}

fn looks_like_ipv4(s: &str) -> bool {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_digit() {
            let mut octets = 0;
            let mut j = i;
            loop {
                let mut digits = 0;
                while j < bytes.len() && bytes[j].is_ascii_digit() && digits < 3 {
                    j += 1;
                    digits += 1;
                }
                if digits == 0 {
                    break;
                }
                octets += 1;
                if octets == 4 {
                    return true;
                }
                if j < bytes.len() && bytes[j] == b'.' {
                    j += 1;
                } else {
                    break;
                }
            }
            i = j.max(i + 1);
        } else {
            i += 1;
        }
    }
    false
}

// A URL pointing at localhost / loopback / reserved test domains is a legit
// placeholder, not project-specific contamination.
fn url_is_allowed(lit: &str) -> bool {
    let l = lit.to_lowercase();
    l.contains("localhost")
        || l.contains("127.0.0.1")
        || l.contains("example.com")
        || l.contains("example.org")
        || l.contains("example.net")
        || l.contains(".test")
}

// Best-effort seconds for a Rust sleep call, for the tests/ duration gate.
fn sleep_seconds(code: &str) -> f64 {
    let units = [
        ("from_secs(", 1.0),
        ("from_millis(", 0.001),
        ("from_micros(", 0.000_001),
        ("from_nanos(", 0.000_000_001),
        ("from_secs_f64(", 1.0),
        ("from_secs_f32(", 1.0),
    ];
    for (marker, scale) in units {
        if let Some(pos) = code.find(marker) {
            let rest = &code[pos + marker.len()..];
            let num: String = rest
                .chars()
                .take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '_')
                .filter(|c| *c != '_')
                .collect();
            if let Ok(v) = num.parse::<f64>() {
                return v * scale;
            }
        }
    }
    // Unknown form (e.g. a Duration variable) - treat as long so it surfaces.
    9999.0
}

fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn basename(path: &str) -> &str {
    path.rsplit(|c| c == '/' || c == '\\').next().unwrap_or(path)
}

fn norm_crate(name: &str) -> String {
    name.trim().to_lowercase().replace('-', "_")
}

// Pull declared dependency crate names (normalized) from widget.json in cwd.
// No serde available, so scan for the "dependencies" array and lift the crate
// name out of each "<crate><op><version>" string.
fn read_deps() -> HashSet<String> {
    let mut deps = HashSet::new();
    let raw = match fs::read_to_string("widget.json") {
        Ok(s) => s,
        Err(_) => return deps,
    };
    let key = "\"dependencies\"";
    let start = match raw.find(key) {
        Some(p) => p,
        None => return deps,
    };
    let after = &raw[start + key.len()..];
    let lb = match after.find('[') {
        Some(p) => p,
        None => return deps,
    };
    let rb = match after[lb..].find(']') {
        Some(p) => lb + p,
        None => after.len(),
    };
    let body = &after[lb + 1..rb];
    let bytes: Vec<char> = body.chars().collect();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == '"' {
            i += 1;
            let mut s = String::new();
            while i < bytes.len() && bytes[i] != '"' {
                s.push(bytes[i]);
                i += 1;
            }
            let crate_name: String = s
                .chars()
                .take_while(|c| is_ident(*c) || *c == '-')
                .collect();
            if !crate_name.is_empty() {
                deps.insert(norm_crate(&crate_name));
            }
        }
        i += 1;
    }
    deps
}

// The widget's own crate name (Cargo.toml [package] name), so its own modules
// are never reported as unlisted imports.
fn read_own_crate() -> String {
    let raw = match fs::read_to_string("Cargo.toml") {
        Ok(s) => s,
        Err(_) => return String::new(),
    };
    for line in raw.lines() {
        let t = line.trim();
        if let Some(rest) = t.strip_prefix("name") {
            let rest = rest.trim_start();
            if let Some(rest) = rest.strip_prefix('=') {
                let v = rest.trim().trim_matches('"');
                return norm_crate(v);
            }
        }
    }
    String::new()
}

// Extract the first path segment of a `use` / `extern crate` statement.
fn use_crate_segment(code: &str) -> Option<String> {
    let mut t = code.trim_start();
    if let Some(rest) = t.strip_prefix("pub ") {
        t = rest.trim_start();
    }
    let rest = if let Some(r) = t.strip_prefix("use ") {
        r
    } else if let Some(r) = t.strip_prefix("extern crate ") {
        r
    } else {
        return None;
    };
    let rest = rest.trim_start().trim_start_matches(':');
    let seg: String = rest
        .chars()
        .take_while(|c| is_ident(*c))
        .collect();
    if seg.is_empty() {
        None
    } else {
        Some(seg)
    }
}

fn is_builtin_crate(seg: &str) -> bool {
    matches!(seg, "std" | "core" | "alloc" | "crate" | "self" | "super")
}

fn scan_file(
    path: &str,
    findings: &mut Vec<Finding>,
    deps: &HashSet<String>,
    own_crate: &str,
) {
    let src = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(_) => return,
    };
    let lex = lex(&src);
    let p = path.replace('\\', "/");
    let is_src = p.contains("/src/") || p.starts_with("src/");
    let sev = if is_src { "block" } else { "warning" };

    let mut push = |kind: &str, line: usize, detail: &str, severity: &str| {
        findings.push(Finding {
            kind: kind.to_string(),
            file: path.to_string(),
            line,
            detail: detail.to_string(),
            severity: severity.to_string(),
        });
    };

    for (idx, code) in lex.sani.iter().enumerate() {
        let line = idx + 1;

        // --- block in src/: library-violating constructs -----------------
        if is_src {
            for m in ["println!", "print!", "eprintln!", "eprint!"] {
                if code.contains(m) {
                    push("print", line, &format!("{} in src/", m), "block");
                    break;
                }
            }
            if code.contains("process::exit") || code.contains("process::abort") {
                push("exit", line, "process::exit/abort in src/", "block");
            }
            if contains_word(code, "unsafe") {
                push("unsafe_block", line, "unsafe in src/", "block");
            }
            if code.contains("env::var") || code.contains("env::vars") {
                push("env_var", line, "env::var in src/", "warning");
            }
            // hardcoded numeric tunable: a const/static bound to a literal
            // other than 0/1.
            let t = code.trim_start();
            let decl = t.strip_prefix("pub ").unwrap_or(t).trim_start();
            if decl.starts_with("const ") || decl.starts_with("static ") {
                if let Some(eq) = code.find('=') {
                    let rhs = code[eq + 1..].trim_start();
                    let num: String =
                        rhs.chars().take_while(|c| c.is_ascii_digit()).collect();
                    if !num.is_empty() && num != "0" && num != "1"
                        && !rhs.starts_with("0x") && !rhs.starts_with("0b")
                    {
                        push("hardcoded_value", line,
                             "hardcoded numeric tunable in src/", "warning");
                    }
                }
            }
        }

        // --- sleep: block in src, warn in tests if > 1s ------------------
        let is_sleep = code.contains("thread::sleep")
            || (contains_word(code, "sleep") && code.contains("Duration"));
        if is_sleep {
            if is_src {
                push("sleep", line, "thread::sleep in src/", "block");
            } else if sleep_seconds(code) > 1.0 {
                push("sleep", line, "long thread::sleep in test", "warning");
            }
        }

        // --- todo!/unimplemented!: warn anywhere -------------------------
        if code.contains("todo!") || code.contains("unimplemented!") {
            push("todo_macro", line, "todo!/unimplemented!", "warning");
        }

        // --- unlisted external crate imports: warn anywhere --------------
        if let Some(seg) = use_crate_segment(code) {
            let norm = norm_crate(&seg);
            if !is_builtin_crate(&seg)
                && norm != own_crate
                && !deps.contains(&norm)
            {
                push("unlisted_import", line, &format!("use {}", seg),
                     "warning");
            }
        }

        // --- missing doc on a public item (src only) ---------------------
        if is_src {
            let t = code.trim_start();
            let is_pub_item = t.starts_with("pub fn ")
                || t.starts_with("pub struct ")
                || t.starts_with("pub enum ")
                || t.starts_with("pub trait ")
                || t.starts_with("pub const ")
                || t.starts_with("pub mod ");
            if is_pub_item {
                let prev_doc = idx > 0
                    && (lex.doc_lines.contains(&idx)
                        || lex.sani[idx - 1].trim_start().starts_with("#["));
                if !prev_doc {
                    let head = t.split('{').next().unwrap_or(t).trim();
                    push("missing_doc", line, head, "warning");
                }
            }
        }
    }

    // --- content checks over string literals ----------------------------
    for (line, lit) in &lex.literals {
        if (lit.contains("http://") || lit.contains("https://"))
            && !url_is_allowed(lit)
        {
            push("hardcoded_url", *line, "URL string literal", "warning");
        }
        if looks_like_ipv4(lit) {
            push("hardcoded_ip", *line, "IP-like string literal", sev);
        }
        let bytes = lit.as_bytes();
        if (bytes.first() == Some(&b'/') && bytes.get(1) != Some(&b'/'))
            || (lit.len() >= 3 && bytes[1] == b':'
                && (bytes[2] == b'\\' || bytes[2] == b'/'))
        {
            push("abs_path", *line, "absolute path literal", sev);
        }
    }

    // credentials: a sanitized line naming a secret bound to a string literal
    let secret_words = [
        "password", "passwd", "secret", "api_key", "apikey", "access_key",
        "auth_token", "private_key",
    ];
    let lit_lines: HashSet<usize> =
        lex.literals.iter().map(|(l, _)| *l).collect();
    for (idx, code) in lex.sani.iter().enumerate() {
        let line = idx + 1;
        if !lit_lines.contains(&line) {
            continue;
        }
        let lower = code.to_lowercase();
        if !lower.contains('=') {
            continue;
        }
        for w in &secret_words {
            if lower.contains(w) {
                push("credential", line, "possible hardcoded credential", sev);
                break;
            }
        }
    }
}

fn main() {
    let deps = read_deps();
    let own_crate = read_own_crate();
    let mut findings: Vec<Finding> = Vec::new();
    for path in env::args().skip(1) {
        // The engine invokes the compiled scanner as
        // `<binary> rust_scanner.rs <files...>` (the harness always passes the
        // scanner source path first); skip ourselves so the scanner's own
        // detection patterns can't masquerade as widget contamination.
        if basename(&path) == "rust_scanner.rs" {
            continue;
        }
        scan_file(&path, &mut findings, &deps, &own_crate);
    }
    let mut out = String::from("[");
    for (i, f) in findings.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        out.push_str(&format!(
            "{{\"kind\":\"{}\",\"file\":\"{}\",\"line\":{},\"detail\":\"{}\",\"severity\":\"{}\"}}",
            json_escape(&f.kind),
            json_escape(&f.file),
            f.line,
            json_escape(&f.detail),
            json_escape(&f.severity),
        ));
    }
    out.push(']');
    // Write via stdout handle rather than println! so this scanner stays
    // self-clean (a println! macro would be a "print" match on its own source).
    let _ = std::io::stdout().write_all(out.as_bytes());
    let _ = std::io::stdout().write_all(b"\n");
}
