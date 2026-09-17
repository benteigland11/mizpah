/*
 * Cartograph contamination scanner for C# widgets.
 *
 * Stdlib-only, runs via .NET 10+ file-based mode:
 *     dotnet run csharp_scanner.cs <file1.cs> <file2.cs> ...
 *
 * Outputs a JSON array of findings to stdout:
 *     [{"kind": "...", "file": "...", "line": N, "detail": "...",
 *       "severity": "block"|"warning"}]
 *
 * A hand-rolled lexer tracks // and multi-line comments, regular /
 * verbatim (@"") / raw (""") / interpolated string literals, and char
 * literals, so banned tokens inside strings or comments never trip. Two
 * views are produced per line:
 *   - noComments: comments blanked, string contents kept (for checks that
 *     look INSIDE strings: paths, URLs, IPs, credentials)
 *   - codeOnly:   comments AND string contents blanked (for API-call
 *     checks: print, exit, sleep, getenv, usings, static fields)
 *
 * Severity contract (mirrors the Go/Rust/Java scanners):
 *   - print/exit: emitted for src/ only, severity block
 *   - abs_path/credential/ip/sleep: block in src/, warning in tests/examples
 *   - url/hardcoded_value/env_var/unlisted_import/static_mutable: warning
 *   - sleep in tests/examples: warn only above ~1s (statically estimated)
 */

using System.Text;
using System.Text.RegularExpressions;

var scanner = new CartographCSharpScanner();
scanner.Run(args);

internal sealed class CartographCSharpScanner
{
    // Drive-letter branch: no preceding word char (else https:// matches
    // via "s://") and no double slash after the colon.
    private static readonly Regex AbsPath = new(
        @"(?:/home/|/Users/|/root/|(?<![\w.])[A-Za-z]:[/\\](?!/))[^\s""']{3,}");
    private static readonly Regex Credential = new(
        @"(?i)(?:api_?key|api_?secret|secret_?key|access_?token|auth_?token|password|passwd|credential)\s*=\s*""[^""]{6,}""");
    private static readonly Regex Url = new(
        @"https?://[^\s""']{4,}");
    private static readonly Regex UrlAllowed = new(
        @"https?://(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.(?:com|org|net)|[\w.-]+\.test)(?:[/:?#]|$)");
    private static readonly Regex Ip = new(
        @"\b(?:\d{1,3}\.){3}\d{1,3}\b");
    private static readonly Regex Print = new(
        @"Console\s*\.\s*(?:Out\s*\.\s*|Error\s*\.\s*)?Write(?:Line)?\s*\(");
    private static readonly Regex Exit = new(
        @"Environment\s*\.\s*(?:Exit|FailFast)\s*\(");
    private static readonly Regex Sleep = new(
        @"Thread\s*\.\s*Sleep\s*\(\s*([\d_]*)");
    private static readonly Regex EnvVar = new(
        @"Environment\s*\.\s*GetEnvironmentVariable\s*\(");
    private static readonly Regex UsingDirective = new(
        @"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;");
    private static readonly Regex NamespaceDecl = new(
        @"^\s*namespace\s+([\w.]+)");
    private static readonly Regex StaticField = new(
        @"^\s*(?:(?:public|internal|protected|private)\s+)*static\s+(?!readonly\b|class\b|void\b)[\w<>\[\],?. ]*?\s\w+\s*[=;]");
    private static readonly Regex HardcodedValue = new(
        @"^\s*(?:(?:public|internal|protected|private)\s+)?const\s+(?:int|long|double|float|decimal|short|byte|uint|ulong|ushort|sbyte)\s+\w+\s*=\s*(-?[\d_]+(?:\.\d+)?(?:[eE][+-]?\d+)?)[MmFfDdLlUu]*\s*;");

    // Using prefixes that never need declaring: the BCL + the test
    // framework the scaffold itself provides + the widget's own namespace.
    private static readonly string[] StdlibPrefixes =
    {
        "System", "Microsoft.CSharp", "Microsoft.VisualBasic", "Xunit",
    };

    private readonly List<string> findings = new();

    public void Run(string[] args)
    {
        List<string> declaredDeps = ReadDeclaredDeps();
        foreach (string arg in args)
        {
            if (Path.GetFileName(arg) == "csharp_scanner.cs")
            {
                continue; // never scan ourselves
            }
            ScanFile(arg, declaredDeps);
        }
        Console.Out.WriteLine("[" + string.Join(",", findings) + "]");
    }

    private void ScanFile(string path, List<string> declaredDeps)
    {
        string norm = path.Replace('\\', '/');
        bool isSrc = norm.Contains("/src/") || norm.StartsWith("src/");
        string content = File.ReadAllText(path, Encoding.UTF8);

        (string[] noComments, string[] codeOnly) = Lex(content);

        string? widgetNs = null;
        foreach (string line in codeOnly)
        {
            Match nm = NamespaceDecl.Match(line);
            if (nm.Success)
            {
                widgetNs = nm.Groups[1].Value.Split('.')[0];
                break;
            }
        }

        for (int i = 0; i < noComments.Length; i++)
        {
            int ln = i + 1;
            string withStrings = noComments[i];
            string code = codeOnly[i];

            // ---- string-content checks --------------------------------
            if (AbsPath.IsMatch(withStrings))
            {
                Emit("abs_path", path, ln, Strip(withStrings),
                    isSrc ? "block" : "warning");
            }
            if (Credential.IsMatch(withStrings))
            {
                Emit("credential", path, ln, Strip(withStrings),
                    isSrc ? "block" : "warning");
            }
            Match um = Url.Match(withStrings);
            if (um.Success && !UrlAllowed.IsMatch(um.Value))
            {
                Emit("hardcoded_url", path, ln, Strip(withStrings),
                    "warning");
            }
            Match im = Ip.Match(withStrings);
            if (im.Success && !code.Contains(im.Value))
            {
                // in a string literal, not a version-ish numeric token in
                // code
                Emit("hardcoded_ip", path, ln, Strip(withStrings),
                    isSrc ? "block" : "warning");
            }

            // ---- code checks ------------------------------------------
            if (isSrc)
            {
                if (Print.IsMatch(code))
                {
                    Emit("print", path, ln, Strip(code), "block");
                }
                if (Exit.IsMatch(code))
                {
                    Emit("exit", path, ln, Strip(code), "block");
                }
                if (EnvVar.IsMatch(code))
                {
                    Emit("env_var", path, ln, Strip(code), "warning");
                }
                if (StaticField.IsMatch(code))
                {
                    Emit("static_mutable", path, ln, Strip(code), "warning");
                }
                Match hv = HardcodedValue.Match(code);
                if (hv.Success)
                {
                    string num = hv.Groups[1].Value.Replace("_", "");
                    if (num != "0" && num != "1" && num != "-1")
                    {
                        Emit("hardcoded_value", path, ln, Strip(code),
                            "warning");
                    }
                }
            }
            Match sm = Sleep.Match(code);
            if (sm.Success)
            {
                if (isSrc)
                {
                    Emit("sleep", path, ln, Strip(code), "block");
                }
                else
                {
                    long millis = ParseMillis(sm.Groups[1].Value);
                    bool timeSpan = code.Contains("TimeSpan");
                    // Thread.Sleep(TimeSpan.FromSeconds(n)): assume >1s.
                    if (timeSpan || millis > 1000)
                    {
                        Emit("sleep", path, ln, Strip(code), "warning");
                    }
                }
            }
            Match ud = UsingDirective.Match(code);
            if (ud.Success && isSrc)
            {
                string ns = ud.Groups[1].Value;
                if (!IsStdlib(ns) && !IsOwn(ns, widgetNs)
                    && !IsDeclared(ns, declaredDeps))
                {
                    Emit("unlisted_import", path, ln, ns, "warning");
                }
            }
        }
    }

    private static bool IsStdlib(string ns)
    {
        foreach (string p in StdlibPrefixes)
        {
            if (ns == p || ns.StartsWith(p + "."))
            {
                return true;
            }
        }
        return false;
    }

    private static bool IsOwn(string ns, string? widgetNs)
    {
        return widgetNs != null
               && (ns == widgetNs || ns.StartsWith(widgetNs + "."));
    }

    /*
     * Loose package matching: a using is "declared" when the declared
     * NuGet package id shares a dotted-segment prefix with the imported
     * namespace in either direction (Newtonsoft.Json ships
     * Newtonsoft.Json.Linq; Dapper ships Dapper). Case-insensitive
     * because package ids are. Warning-only check, so false negatives
     * are acceptable.
     */
    private static bool IsDeclared(string ns, List<string> deps)
    {
        string[] seg = ns.Split('.');
        string two = seg.Length >= 2 ? seg[0] + "." + seg[1] : seg[0];
        foreach (string dep in deps)
        {
            string bare = Regex.Split(dep, "[><=!~]")[0].Trim();
            if (bare.Length == 0)
            {
                continue;
            }
            if (bare.StartsWith(two, StringComparison.OrdinalIgnoreCase)
                || two.StartsWith(bare, StringComparison.OrdinalIgnoreCase)
                || ns.StartsWith(bare, StringComparison.OrdinalIgnoreCase)
                || bare.StartsWith(seg[0],
                    StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    /* Read tech_stack.dependencies from widget.json in CWD (minimal JSON). */
    private static List<string> ReadDeclaredDeps()
    {
        var deps = new List<string>();
        if (!File.Exists("widget.json"))
        {
            return deps;
        }
        try
        {
            string json = File.ReadAllText("widget.json", Encoding.UTF8);
            Match arr = Regex.Match(json,
                "\"dependencies\"\\s*:\\s*\\[([^\\]]*)\\]");
            if (arr.Success)
            {
                foreach (Match s in Regex.Matches(arr.Groups[1].Value,
                             "\"([^\"]+)\""))
                {
                    deps.Add(s.Groups[1].Value);
                }
            }
        }
        catch (IOException)
        {
            // unreadable widget.json -> treat as no declared deps
        }
        return deps;
    }

    private static long ParseMillis(string literal)
    {
        if (string.IsNullOrEmpty(literal))
        {
            return long.MaxValue; // non-literal argument: assume long sleep
        }
        return long.TryParse(literal.Replace("_", ""), out long v)
            ? v : long.MaxValue;
    }

    // ---- lexer ------------------------------------------------------------

    /*
     * Returns (noComments[], codeOnly[]) line views. Comment characters are
     * replaced with spaces in both; string/char literal CONTENTS are kept in
     * noComments but replaced with spaces in codeOnly (quotes kept so
     * patterns like ="..." still anchor in noComments). Interpolated
     * strings are treated as plain strings (interpolation holes count as
     * string content - acceptable for warning-grade checks).
     */
    private static (string[], string[]) Lex(string content)
    {
        var noC = new StringBuilder(content.Length);
        var codeO = new StringBuilder(content.Length);
        int i = 0;
        int n = content.Length;
        const int CODE = 0, LINE_COMMENT = 1, BLOCK_COMMENT = 2,
            STRING = 3, VERBATIM = 4, RAW = 5, CHAR = 6;
        int state = CODE;
        while (i < n)
        {
            char c = content[i];
            char c1 = i + 1 < n ? content[i + 1] : '\0';
            char c2 = i + 2 < n ? content[i + 2] : '\0';
            if (c == '\n')
            {
                if (state == LINE_COMMENT)
                {
                    state = CODE;
                }
                noC.Append('\n');
                codeO.Append('\n');
                i++;
                continue;
            }
            switch (state)
            {
                case CODE:
                    if (c == '/' && c1 == '/')
                    {
                        state = LINE_COMMENT;
                        noC.Append("  ");
                        codeO.Append("  ");
                        i += 2;
                    }
                    else if (c == '/' && c1 == '*')
                    {
                        state = BLOCK_COMMENT;
                        noC.Append("  ");
                        codeO.Append("  ");
                        i += 2;
                    }
                    else if (c == '@' || c == '$')
                    {
                        // Prefix run before a string: @"...", $"...", $@"..."
                        int j = i;
                        bool verbatim = false;
                        while (j < n && (content[j] == '@'
                                         || content[j] == '$'))
                        {
                            if (content[j] == '@')
                            {
                                verbatim = true;
                            }
                            j++;
                        }
                        if (j < n && content[j] == '"')
                        {
                            for (int k = i; k < j; k++)
                            {
                                noC.Append(content[k]);
                                codeO.Append(content[k]);
                            }
                            i = j;
                            char d1 = i + 1 < n ? content[i + 1] : '\0';
                            char d2 = i + 2 < n ? content[i + 2] : '\0';
                            if (d1 == '"' && d2 == '"')
                            {
                                state = RAW;
                                noC.Append("\"\"\"");
                                codeO.Append("   ");
                                i += 3;
                            }
                            else
                            {
                                state = verbatim ? VERBATIM : STRING;
                                noC.Append('"');
                                codeO.Append('"');
                                i++;
                            }
                        }
                        else
                        {
                            noC.Append(c);
                            codeO.Append(c);
                            i++;
                        }
                    }
                    else if (c == '"' && c1 == '"' && c2 == '"')
                    {
                        state = RAW;
                        noC.Append("\"\"\"");
                        codeO.Append("   ");
                        i += 3;
                    }
                    else if (c == '"')
                    {
                        state = STRING;
                        noC.Append('"');
                        codeO.Append('"');
                        i++;
                    }
                    else if (c == '\'')
                    {
                        state = CHAR;
                        noC.Append('\'');
                        codeO.Append(' ');
                        i++;
                    }
                    else
                    {
                        noC.Append(c);
                        codeO.Append(c);
                        i++;
                    }
                    break;
                case LINE_COMMENT:
                    noC.Append(' ');
                    codeO.Append(' ');
                    i++;
                    break;
                case BLOCK_COMMENT:
                    if (c == '*' && c1 == '/')
                    {
                        state = CODE;
                        noC.Append("  ");
                        codeO.Append("  ");
                        i += 2;
                    }
                    else
                    {
                        noC.Append(' ');
                        codeO.Append(' ');
                        i++;
                    }
                    break;
                case STRING:
                    if (c == '\\' && i + 1 < n)
                    {
                        noC.Append(c).Append(c1);
                        codeO.Append("  ");
                        i += 2;
                    }
                    else if (c == '"')
                    {
                        state = CODE;
                        noC.Append('"');
                        codeO.Append('"');
                        i++;
                    }
                    else
                    {
                        noC.Append(c);
                        codeO.Append(' ');
                        i++;
                    }
                    break;
                case VERBATIM:
                    if (c == '"' && c1 == '"')
                    {
                        noC.Append("\"\"");
                        codeO.Append("  ");
                        i += 2;
                    }
                    else if (c == '"')
                    {
                        state = CODE;
                        noC.Append('"');
                        codeO.Append('"');
                        i++;
                    }
                    else
                    {
                        noC.Append(c);
                        codeO.Append(' ');
                        i++;
                    }
                    break;
                case RAW:
                    if (c == '"' && c1 == '"' && c2 == '"')
                    {
                        state = CODE;
                        noC.Append("\"\"\"");
                        codeO.Append("   ");
                        i += 3;
                    }
                    else
                    {
                        noC.Append(c);
                        codeO.Append(' ');
                        i++;
                    }
                    break;
                case CHAR:
                default:
                    if (c == '\\' && i + 1 < n)
                    {
                        noC.Append(c).Append(c1);
                        codeO.Append("  ");
                        i += 2;
                    }
                    else if (c == '\'')
                    {
                        state = CODE;
                        noC.Append('\'');
                        codeO.Append(' ');
                        i++;
                    }
                    else
                    {
                        noC.Append(c);
                        codeO.Append(' ');
                        i++;
                    }
                    break;
            }
        }
        return (noC.ToString().Split('\n'),
            codeO.ToString().Split('\n'));
    }

    // ---- output -----------------------------------------------------------

    private static string Strip(string s)
    {
        string t = s.Trim();
        return t.Length > 160 ? t.Substring(0, 160) : t;
    }

    private void Emit(string kind, string file, int line, string detail,
        string severity)
    {
        findings.Add(
            $"{{\"kind\":\"{Esc(kind)}\",\"file\":\"{Esc(file)}\","
            + $"\"line\":{line},\"detail\":\"{Esc(detail)}\","
            + $"\"severity\":\"{Esc(severity)}\"}}");
    }

    private static string Esc(string s)
    {
        var b = new StringBuilder(s.Length + 8);
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': b.Append("\\\""); break;
                case '\\': b.Append("\\\\"); break;
                case '\n': b.Append("\\n"); break;
                case '\r': b.Append("\\r"); break;
                case '\t': b.Append("\\t"); break;
                default:
                    if (c < 0x20)
                    {
                        b.Append($"\\u{(int)c:x4}");
                    }
                    else
                    {
                        b.Append(c);
                    }
                    break;
            }
        }
        return b.ToString();
    }
}
