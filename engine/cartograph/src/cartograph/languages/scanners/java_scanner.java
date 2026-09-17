/**
 * Cartograph contamination scanner for Java widgets.
 *
 * Stdlib-only, runs in JDK 11+ single-file mode:
 *     java java_scanner.java <file1.java> <file2.java> ...
 *
 * Outputs a JSON array of findings to stdout:
 *     [{"kind": "...", "file": "...", "line": N, "detail": "...",
 *       "severity": "block"|"warning"}]
 *
 * A hand-rolled lexer tracks // and multi-line comments, string literals,
 * char literals, and Java 15+ text blocks, so banned tokens inside strings
 * or comments never trip. Two views are produced per line:
 *   - noComments: comments blanked, string contents kept (for checks that
 *     look INSIDE strings: paths, URLs, IPs, credentials)
 *   - codeOnly:   comments AND string contents blanked (for API-call
 *     checks: print, exit, sleep, getenv, imports, static fields)
 *
 * Severity contract (mirrors the Go/Rust scanners):
 *   - print/exit: emitted for src/ only, severity block
 *   - abs_path/credential/ip/sleep: block in src/, warning in tests/examples
 *   - url/hardcoded_value/env_var/unlisted_import/static_mutable: warning
 *   - sleep in tests/examples: warn only above ~1s (statically estimated)
 */

import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class java_scanner {

    private static final Pattern ABS_PATH = Pattern.compile(
            "(?:/home/|/Users/|/root/|[A-Za-z]:[/\\\\])[^\\s\"']{3,}");
    private static final Pattern CREDENTIAL = Pattern.compile(
            "(?i)(?:api_?key|api_?secret|secret_?key|access_?token|auth_?token|password|passwd|credential)\\s*=\\s*\"[^\"]{6,}\"");
    private static final Pattern URL = Pattern.compile(
            "https?://[^\\s\"']{4,}");
    private static final Pattern URL_ALLOWED = Pattern.compile(
            "https?://(?:localhost|127\\.0\\.0\\.1|(?:[\\w-]+\\.)*example\\.(?:com|org|net)|[\\w.-]+\\.test)(?:[/:?#]|$)");
    private static final Pattern IP = Pattern.compile(
            "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b");
    private static final Pattern PRINT = Pattern.compile(
            "System\\s*\\.\\s*(?:out|err)\\s*\\.\\s*(?:print|println|printf|write)\\s*\\(");
    private static final Pattern EXIT = Pattern.compile(
            "(?:System\\s*\\.\\s*exit\\s*\\(|Runtime\\s*\\.\\s*getRuntime\\s*\\(\\s*\\)\\s*\\.\\s*(?:halt|exit)\\s*\\()");
    private static final Pattern SLEEP = Pattern.compile(
            "(?:Thread\\s*\\.\\s*sleep|TimeUnit\\s*\\.\\s*\\w+\\s*\\.\\s*sleep)\\s*\\(\\s*([\\d_]*)");
    private static final Pattern ENV_VAR = Pattern.compile(
            "System\\s*\\.\\s*(?:getenv|getProperty)\\s*\\(");
    private static final Pattern IMPORT = Pattern.compile(
            "^\\s*import\\s+(?:static\\s+)?([\\w.]+)\\s*;");
    private static final Pattern STATIC_FIELD = Pattern.compile(
            "^\\s*(?:public\\s+|protected\\s+|private\\s+)?static\\s+(?!final\\b)\\w[\\w<>\\[\\], ]*\\s+\\w+\\s*[=;]");
    private static final Pattern HARDCODED_VALUE = Pattern.compile(
            "^\\s*(?:public\\s+|protected\\s+|private\\s+)?(?:static\\s+)?final\\s+(?:int|long|double|float|short|byte)\\s+\\w+\\s*=\\s*(-?[\\d_]+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)[LlFfDd]?\\s*;");

    // Import prefixes that never need declaring: JDK + the test framework
    // the scaffold itself provides + the widget's own local packages.
    private static final String[] STDLIB_PREFIXES = {
            "java.", "javax.", "jdk.", "com.sun.", "org.w3c.", "org.xml.",
            "org.junit.",
    };

    private static final List<String> findings = new ArrayList<>();

    public static void main(String[] args) throws IOException {
        List<String> declaredDeps = readDeclaredDeps();
        for (String arg : args) {
            Path p = Paths.get(arg);
            String base = p.getFileName().toString();
            if (base.equals("java_scanner.java")) {
                continue; // never scan ourselves
            }
            scanFile(p, declaredDeps);
        }
        PrintStream out = new PrintStream(System.out, true,
                StandardCharsets.UTF_8);
        out.println("[" + String.join(",", findings) + "]");
    }

    private static void scanFile(Path path, List<String> declaredDeps)
            throws IOException {
        String norm = path.toString().replace('\\', '/');
        boolean isSrc = norm.contains("/src/") || norm.startsWith("src/");
        String content = Files.readString(path, StandardCharsets.UTF_8);

        String[][] views = lex(content);
        String[] noComments = views[0];
        String[] codeOnly = views[1];

        String widgetPkg = null;
        for (String line : codeOnly) {
            Matcher pm = Pattern.compile("^\\s*package\\s+([\\w.]+)\\s*;")
                    .matcher(line);
            if (pm.find()) {
                widgetPkg = pm.group(1).split("\\.")[0];
                break;
            }
        }

        for (int i = 0; i < noComments.length; i++) {
            int ln = i + 1;
            String withStrings = noComments[i];
            String code = codeOnly[i];

            // ---- string-content checks --------------------------------
            if (ABS_PATH.matcher(withStrings).find()) {
                emit("abs_path", path, ln, strip(withStrings),
                        isSrc ? "block" : "warning");
            }
            if (CREDENTIAL.matcher(withStrings).find()) {
                emit("credential", path, ln, strip(withStrings),
                        isSrc ? "block" : "warning");
            }
            Matcher um = URL.matcher(withStrings);
            if (um.find() && !URL_ALLOWED.matcher(um.group()).find()) {
                emit("hardcoded_url", path, ln, strip(withStrings),
                        "warning");
            }
            Matcher im = IP.matcher(withStrings);
            if (im.find() && !code.contains(im.group())) {
                // in a string literal (not, say, a version-ish numeric token
                // in code) - and skip x.y.z.w where every octet is 1 digit
                // only when it's clearly a version; keep it simple: flag all.
                emit("hardcoded_ip", path, ln, strip(withStrings),
                        isSrc ? "block" : "warning");
            }

            // ---- code checks ------------------------------------------
            if (isSrc) {
                if (PRINT.matcher(code).find()) {
                    emit("print", path, ln, strip(code), "block");
                }
                if (EXIT.matcher(code).find()) {
                    emit("exit", path, ln, strip(code), "block");
                }
                if (ENV_VAR.matcher(code).find()) {
                    emit("env_var", path, ln, strip(code), "warning");
                }
                if (STATIC_FIELD.matcher(code).find()) {
                    emit("static_mutable", path, ln, strip(code), "warning");
                }
                Matcher hv = HARDCODED_VALUE.matcher(code);
                if (hv.find()) {
                    String num = hv.group(1).replace("_", "");
                    if (!num.equals("0") && !num.equals("1")
                            && !num.equals("-1")) {
                        emit("hardcoded_value", path, ln, strip(code),
                                "warning");
                    }
                }
            }
            Matcher sm = SLEEP.matcher(code);
            if (sm.find()) {
                if (isSrc) {
                    emit("sleep", path, ln, strip(code), "block");
                } else {
                    long millis = parseMillis(sm.group(1));
                    boolean timeUnit = code.contains("TimeUnit");
                    // TimeUnit.SECONDS.sleep(n) is n seconds; assume >1s.
                    if (timeUnit || millis > 1000) {
                        emit("sleep", path, ln, strip(code), "warning");
                    }
                }
            }
            Matcher imp = IMPORT.matcher(code);
            if (imp.find() && isSrc) {
                String fqcn = imp.group(1);
                if (!isStdlib(fqcn) && !isOwn(fqcn, widgetPkg)
                        && !isDeclared(fqcn, declaredDeps)) {
                    emit("unlisted_import", path, ln, fqcn, "warning");
                }
            }
        }
    }

    private static boolean isStdlib(String fqcn) {
        for (String p : STDLIB_PREFIXES) {
            if (fqcn.startsWith(p)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isOwn(String fqcn, String widgetPkg) {
        return widgetPkg != null && (fqcn.equals(widgetPkg)
                || fqcn.startsWith(widgetPkg + "."));
    }

    /**
     * Loose coordinate matching: an import is "declared" when the declared
     * dep's group or artifact shares a prefix with the import's package.
     * Maven groups don't always equal package roots (com.google.code.gson
     * ships com.google.gson), so match on any dotted-segment overlap of the
     * first two package segments in either direction. Warning-only check,
     * so false negatives are acceptable.
     */
    private static boolean isDeclared(String fqcn, List<String> deps) {
        String[] seg = fqcn.split("\\.");
        String two = seg.length >= 2 ? seg[0] + "." + seg[1] : seg[0];
        String last = seg.length >= 2 ? seg[seg.length - 2] : seg[0];
        for (String dep : deps) {
            String bare = dep.split("[><=!~]")[0].trim();
            if (bare.isEmpty()) {
                continue;
            }
            String group = bare.split(":")[0];
            String artifact = bare.contains(":") ? bare.split(":")[1] : "";
            if (group.startsWith(two) || two.startsWith(group)
                    || fqcn.contains(artifact.replace('-', '.'))
                    || (!artifact.isEmpty() && fqcn.contains(artifact))
                    || (!artifact.isEmpty() && artifact.contains(last))) {
                return true;
            }
        }
        return false;
    }

    /** Read tech_stack.dependencies from widget.json in CWD (minimal JSON). */
    private static List<String> readDeclaredDeps() {
        List<String> deps = new ArrayList<>();
        Path wj = Paths.get("widget.json");
        if (!Files.isReadable(wj)) {
            return deps;
        }
        try {
            String json = Files.readString(wj, StandardCharsets.UTF_8);
            Matcher arr = Pattern.compile(
                    "\"dependencies\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
            if (arr.find()) {
                Matcher s = Pattern.compile("\"([^\"]+)\"")
                        .matcher(arr.group(1));
                while (s.find()) {
                    deps.add(s.group(1));
                }
            }
        } catch (IOException ignored) {
            // unreadable widget.json -> treat as no declared deps
        }
        return deps;
    }

    private static long parseMillis(String literal) {
        if (literal == null || literal.isEmpty()) {
            return Long.MAX_VALUE; // non-literal argument: assume long sleep
        }
        try {
            return Long.parseLong(literal.replace("_", ""));
        } catch (NumberFormatException e) {
            return Long.MAX_VALUE;
        }
    }

    // ---- lexer ------------------------------------------------------------

    /**
     * Returns {noComments[], codeOnly[]} line views. Comment characters are
     * replaced with spaces in both; string/char literal CONTENTS are kept in
     * noComments but replaced with spaces in codeOnly (quotes kept so
     * patterns like ="..." still anchor in noComments).
     */
    private static String[][] lex(String content) {
        StringBuilder noC = new StringBuilder(content.length());
        StringBuilder codeO = new StringBuilder(content.length());
        int i = 0;
        int n = content.length();
        final int CODE = 0;
        final int LINE_COMMENT = 1;
        final int BLOCK_COMMENT = 2;
        final int STRING = 3;
        final int CHAR = 4;
        final int TEXT_BLOCK = 5;
        int state = CODE;
        while (i < n) {
            char c = content.charAt(i);
            char c1 = i + 1 < n ? content.charAt(i + 1) : '\0';
            char c2 = i + 2 < n ? content.charAt(i + 2) : '\0';
            if (c == '\n') {
                if (state == LINE_COMMENT) {
                    state = CODE;
                }
                noC.append('\n');
                codeO.append('\n');
                i++;
                continue;
            }
            switch (state) {
                case CODE:
                    if (c == '/' && c1 == '/') {
                        state = LINE_COMMENT;
                        noC.append("  ");
                        codeO.append("  ");
                        i += 2;
                    } else if (c == '/' && c1 == '*') {
                        state = BLOCK_COMMENT;
                        noC.append("  ");
                        codeO.append("  ");
                        i += 2;
                    } else if (c == '"' && c1 == '"' && c2 == '"') {
                        state = TEXT_BLOCK;
                        noC.append("\"\"\"");
                        codeO.append("   ");
                        i += 3;
                    } else if (c == '"') {
                        state = STRING;
                        noC.append('"');
                        codeO.append('"');
                        i++;
                    } else if (c == '\'') {
                        state = CHAR;
                        noC.append('\'');
                        codeO.append(' ');
                        i++;
                    } else {
                        noC.append(c);
                        codeO.append(c);
                        i++;
                    }
                    break;
                case LINE_COMMENT:
                    noC.append(' ');
                    codeO.append(' ');
                    i++;
                    break;
                case BLOCK_COMMENT:
                    if (c == '*' && c1 == '/') {
                        state = CODE;
                        noC.append("  ");
                        codeO.append("  ");
                        i += 2;
                    } else {
                        noC.append(' ');
                        codeO.append(' ');
                        i++;
                    }
                    break;
                case STRING:
                    if (c == '\\' && i + 1 < n) {
                        noC.append(c).append(c1);
                        codeO.append("  ");
                        i += 2;
                    } else if (c == '"') {
                        state = CODE;
                        noC.append('"');
                        codeO.append('"');
                        i++;
                    } else {
                        noC.append(c);
                        codeO.append(' ');
                        i++;
                    }
                    break;
                case CHAR:
                    if (c == '\\' && i + 1 < n) {
                        noC.append(c).append(c1);
                        codeO.append("  ");
                        i += 2;
                    } else if (c == '\'') {
                        state = CODE;
                        noC.append('\'');
                        codeO.append(' ');
                        i++;
                    } else {
                        noC.append(c);
                        codeO.append(' ');
                        i++;
                    }
                    break;
                case TEXT_BLOCK:
                default:
                    if (c == '"' && c1 == '"' && c2 == '"') {
                        state = CODE;
                        noC.append("\"\"\"");
                        codeO.append("   ");
                        i += 3;
                    } else {
                        noC.append(c);
                        codeO.append(' ');
                        i++;
                    }
                    break;
            }
        }
        return new String[][] {
                noC.toString().split("\n", -1),
                codeO.toString().split("\n", -1),
        };
    }

    // ---- output -----------------------------------------------------------

    private static String strip(String s) {
        String t = s.trim();
        return t.length() > 160 ? t.substring(0, 160) : t;
    }

    private static void emit(String kind, Path file, int line, String detail,
                             String severity) {
        findings.add(String.format(
                "{\"kind\":\"%s\",\"file\":\"%s\",\"line\":%d,"
                        + "\"detail\":\"%s\",\"severity\":\"%s\"}",
                esc(kind), esc(file.toString()), line, esc(detail),
                esc(severity)));
    }

    private static String esc(String s) {
        StringBuilder b = new StringBuilder(s.length() + 8);
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        return b.toString();
    }
}
