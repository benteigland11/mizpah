/**
 * Cartograph JavaScript/TypeScript source scanner.
 *
 * This scanner is token-based rather than line-regex-based so it can handle:
 * - multiline imports / require calls
 * - JSX / TSX-adjacent syntax
 * - comments and strings without false positives from comment text
 * - common JS/TS call patterns with whitespace/newline variation
 *
 * It is still intentionally narrow: this is a policy scanner, not a full AST.
 */

const fs = require('fs')

const NODE_BUILTINS = new Set([
  'assert', 'buffer', 'child_process', 'cluster', 'console', 'constants',
  'crypto', 'dgram', 'dns', 'domain', 'events', 'fs', 'http', 'https',
  'module', 'net', 'os', 'path', 'perf_hooks', 'process', 'punycode',
  'querystring', 'readline', 'repl', 'stream', 'string_decoder', 'timers',
  'tls', 'tty', 'url', 'util', 'v8', 'vm', 'worker_threads', 'zlib',
])

const RISKY_IMPORTS = new Set([
  'fs', 'child_process', 'net', 'http', 'https', 'dgram', 'cluster', 'worker_threads',
])

// Test frameworks we mandate (or that are canonical) should never be flagged
// as unlisted in test/example files. They sit in package.json devDependencies,
// not widget.json tech_stack dependencies, and forcing users to either
// duplicate them or override every checkin is bad UX.
const TEST_FRAMEWORKS = new Set([
  'vitest', 'jest', 'mocha', 'chai', 'jasmine', 'sinon', 'supertest',
  'playwright', '@playwright/test', 'cypress', 'karma', '@jest/globals',
  '@vitest/ui',
])

const CREDENTIAL_RE = /(?:api_key|api_secret|secret_key|access_token|auth_token|password|passwd|credential)\s*=/i
const ABS_PATH_RE = /(?:\/home\/|\/Users\/|\/root\/|[A-Za-z]:[/\\])/
const URL_RE = /^https?:\/\/(?!(?:localhost|127\.0\.0\.1|[\w-]*\.?example\.com|[\w.-]+\.test(?:[\/:"'#?]|$)|schemas?\.))/i
const IP_RE = /^(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?$/

function loadDeclaredDeps() {
  try {
    const data = JSON.parse(fs.readFileSync('widget.json', 'utf-8'))
    const deps = (data.tech_stack || {}).dependencies || []
    return new Set(deps.map(d => String(d).split(/[><=!~;\[]/)[0].trim().toLowerCase()))
  } catch {
    return new Set()
  }
}

const declaredDeps = loadDeclaredDeps()

function lineOf(content, index) {
  let line = 1
  for (let i = 0; i < index; i++) {
    if (content[i] === '\n') line++
  }
  return line
}

function addFinding(findings, file, kind, line, detail, severity) {
  const finding = { file, kind, line, detail }
  if (severity) finding.severity = severity
  findings.push(finding)
}

function isIdentStart(ch) {
  return /[A-Za-z_$]/.test(ch)
}

function isIdentPart(ch) {
  return /[A-Za-z0-9_$]/.test(ch)
}

function isDigit(ch) {
  return /[0-9]/.test(ch)
}

function tokenize(content) {
  const tokens = []
  let i = 0
  let prevSignificant = null

  function push(type, value, start, end, extra = {}) {
    const tok = { type, value, start, end, line: lineOf(content, start), ...extra }
    tokens.push(tok)
    if (type !== 'newline') prevSignificant = tok
  }

  while (i < content.length) {
    const ch = content[i]
    const next = i + 1 < content.length ? content[i + 1] : ''

    if (ch === '\n') {
      push('newline', '\n', i, i + 1)
      i++
      continue
    }

    if (/\s/.test(ch)) {
      i++
      continue
    }

    if (ch === '/' && next === '/') {
      i += 2
      while (i < content.length && content[i] !== '\n') i++
      continue
    }

    if (ch === '/' && next === '*') {
      i += 2
      while (i < content.length && !(content[i] === '*' && content[i + 1] === '/')) i++
      i = Math.min(i + 2, content.length)
      continue
    }

    if (ch === '"' || ch === "'") {
      const quote = ch
      const start = i
      let value = ''
      i++
      while (i < content.length) {
        const c = content[i]
        if (c === '\\') {
          value += c
          if (i + 1 < content.length) value += content[i + 1]
          i += 2
          continue
        }
        if (c === quote) {
          i++
          break
        }
        value += c
        i++
      }
      push('string', value, start, i, { quote })
      continue
    }

    if (ch === '`') {
      const start = i
      let value = ''
      i++
      let templateDepth = 0
      while (i < content.length) {
        const c = content[i]
        const n = i + 1 < content.length ? content[i + 1] : ''
        if (c === '\\') {
          value += c
          if (i + 1 < content.length) value += content[i + 1]
          i += 2
          continue
        }
        if (templateDepth === 0 && c === '`') {
          i++
          break
        }
        if (c === '$' && n === '{') {
          templateDepth++
          value += '${'
          i += 2
          continue
        }
        if (templateDepth > 0) {
          if (c === '{') templateDepth++
          else if (c === '}') templateDepth--
        }
        value += c
        i++
      }
      push('template', value, start, i)
      continue
    }

    if (isIdentStart(ch)) {
      const start = i
      i++
      while (i < content.length && isIdentPart(content[i])) i++
      const value = content.slice(start, i)
      push('ident', value, start, i)
      continue
    }

    if (isDigit(ch) || (ch === '-' && isDigit(next))) {
      const start = i
      i++
      while (i < content.length && /[0-9._eE+-]/.test(content[i])) i++
      push('number', content.slice(start, i), start, i)
      continue
    }

    const three = content.slice(i, i + 3)
    const two = content.slice(i, i + 2)
    if (['===', '!==', '=>'].includes(three)) {
      push('punct', three, i, i + 3)
      i += 3
      continue
    }
    if (['?.', '??', '&&', '||', '==', '!=', '<=', '>='].includes(two)) {
      push('punct', two, i, i + 2)
      i += 2
      continue
    }

    push('punct', ch, i, i + 1)
    i++
  }

  return tokens
}

function nextSignificant(tokens, idx) {
  for (let i = idx + 1; i < tokens.length; i++) {
    if (tokens[i].type !== 'newline') return tokens[i]
  }
  return null
}

function collectArgTokens(tokens, openIdx) {
  const args = []
  let depth = 0
  for (let i = openIdx; i < tokens.length; i++) {
    const tok = tokens[i]
    if (tok.type === 'punct' && tok.value === '(') {
      depth++
      if (depth === 1) continue
    }
    if (tok.type === 'punct' && tok.value === ')') {
      depth--
      if (depth === 0) break
    }
    if (depth >= 1) args.push(tok)
  }
  return args
}

function splitTopLevelArgs(tokens) {
  const args = [[]]
  let paren = 0
  let brace = 0
  let bracket = 0
  for (const tok of tokens) {
    if (tok.type === 'punct') {
      if (tok.value === '(') paren++
      else if (tok.value === ')') paren--
      else if (tok.value === '{') brace++
      else if (tok.value === '}') brace--
      else if (tok.value === '[') bracket++
      else if (tok.value === ']') bracket--
      else if (tok.value === ',' && paren === 0 && brace === 0 && bracket === 0) {
        args.push([])
        continue
      }
    }
    args[args.length - 1].push(tok)
  }
  return args
}

function firstMeaningful(tokens) {
  return tokens.find(t => t.type !== 'newline') || null
}

function bareImportName(specifier) {
  if (!specifier || specifier.startsWith('.') || specifier.startsWith('/')) return null
  if (specifier.startsWith('@')) {
    const parts = specifier.split('/')
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : specifier
  }
  return specifier.split('/')[0]
}

function addImportFinding(findings, filename, moduleName, line, unlistedSeverity, inTestOrExample) {
  const bare = bareImportName(moduleName)
  if (!bare) return
  // 'node:crypto' is the explicit built-in prefix syntax - strip it before lookup
  const lower = bare.replace(/^node:/, '').toLowerCase()
  if (RISKY_IMPORTS.has(lower)) {
    addFinding(findings, filename, 'risky_import', line, `import '${bare}' - flagged for review`)
  }
  // Test frameworks are mandated by validation and declared in package.json
  // devDependencies, not widget.json. Allowlist them in test/example files.
  if (inTestOrExample && TEST_FRAMEWORKS.has(lower)) return
  if (!NODE_BUILTINS.has(lower) && !declaredDeps.has(lower)) {
    addFinding(findings, filename, 'unlisted_import', line, `import '${bare}' - not in widget.json dependencies`, unlistedSeverity)
  }
}

function detectImports(tokens, filename, findings, inTests, inExamples) {
  const unlistedSeverity = (inTests || inExamples) ? 'warning' : 'block'
  const inTestOrExample = inTests || inExamples
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i]
    if (tok.type === 'ident' && tok.value === 'require') {
      const open = nextSignificant(tokens, i)
      if (!open || open.type !== 'punct' || open.value !== '(') continue
      const args = splitTopLevelArgs(collectArgTokens(tokens, tokens.indexOf(open)))
      const first = firstMeaningful(args[0] || [])
      if (first && first.type === 'string') {
        addImportFinding(findings, filename, first.value, tok.line, unlistedSeverity, inTestOrExample)
      }
      continue
    }

    if (tok.type === 'ident' && tok.value === 'import') {
      let j = i + 1
      while (j < tokens.length) {
        const cur = tokens[j]
        if (cur.type === 'punct' && cur.value === ';') break
        if (cur.type === 'newline' && tokens[j - 1]?.type === 'string') break
        if (cur.type === 'string') {
          addImportFinding(findings, filename, cur.value, cur.line, unlistedSeverity, inTestOrExample)
          break
        }
        j++
      }
    }
  }
}

function detectMemberCall(tokens, root, memberSet, kind, makeDetail, findings, filename) {
  for (let i = 0; i < tokens.length - 3; i++) {
    if (tokens[i].type !== 'ident' || tokens[i].value !== root) continue
    if (tokens[i + 1].type !== 'punct' || tokens[i + 1].value !== '.') continue
    if (tokens[i + 2].type !== 'ident' || !memberSet.has(tokens[i + 2].value)) continue
    if (tokens[i + 3].type !== 'punct' || tokens[i + 3].value !== '(') continue
    addFinding(findings, filename, kind, tokens[i].line, makeDetail(tokens[i + 2].value))
  }
}

function detectMemberAccess(tokens, root, memberSet, kind, makeDetail, findings, filename, severity) {
  for (let i = 0; i < tokens.length - 2; i++) {
    if (tokens[i].type !== 'ident' || tokens[i].value !== root) continue
    if (tokens[i + 1].type !== 'punct' || tokens[i + 1].value !== '.') continue
    if (tokens[i + 2].type !== 'ident' || !memberSet.has(tokens[i + 2].value)) continue
    addFinding(findings, filename, kind, tokens[i].line, makeDetail(tokens[i + 2].value), severity)
  }
}

function detectSleepCalls(tokens, filename, findings, inTests, inExamples) {
  function durationWarning(line, name, args) {
    const second = firstMeaningful(args[1] || [])
    if (second && second.type === 'number') {
      const n = parseInt(second.value, 10)
      if (Number.isFinite(n) && n > 1000) {
        addFinding(findings, filename, 'sleep', line, `${name}(_, ${n}) - consider reducing sleep duration`, 'warning')
      }
    }
  }

  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i]
    if (tok.type === 'ident' && (tok.value === 'setTimeout' || tok.value === 'setInterval')) {
      const open = nextSignificant(tokens, i)
      if (!open || open.type !== 'punct' || open.value !== '(') continue
      if (!inTests && !inExamples) {
        addFinding(findings, filename, 'sleep', tok.line, `${tok.value}() call - widgets must not block or delay the caller`, 'block')
      } else {
        const args = splitTopLevelArgs(collectArgTokens(tokens, tokens.indexOf(open)))
        durationWarning(tok.line, tok.value, args)
      }
      continue
    }

    if (tok.type === 'ident' && tok.value === 'Bun') {
      if (tokens[i + 1]?.type !== 'punct' || tokens[i + 1]?.value !== '.') continue
      if (tokens[i + 2]?.type !== 'ident' || !['sleep', 'sleepSync'].includes(tokens[i + 2].value)) continue
      if (tokens[i + 3]?.type !== 'punct' || tokens[i + 3].value !== '(') continue
      const name = `Bun.${tokens[i + 2].value}`
      if (!inTests && !inExamples) {
        addFinding(findings, filename, 'sleep', tok.line, `${name}() call - widgets must not block or delay the caller`, 'block')
      } else {
        const args = splitTopLevelArgs(collectArgTokens(tokens, i + 3))
        const first = firstMeaningful(args[0] || [])
        if (first && first.type === 'number') {
          const n = parseInt(first.value, 10)
          if (Number.isFinite(n) && n > 1000) {
            addFinding(findings, filename, 'sleep', tok.line, `${name}(${n}) - consider reducing sleep duration`, 'warning')
          }
        }
      }
    }
  }
}

function detectAssignments(tokens, filename, findings, inTests, inExamples) {
  // hardcoded_value: src only - tests and examples legitimately use fixtures,
  // expected values, and demo constants (matches Python/Nim behavior).
  if (inTests || inExamples) return
  for (let i = 0; i < tokens.length - 3; i++) {
    const start = tokens[i]
    if (start.type !== 'ident' || !['const', 'let'].includes(start.value)) continue
    const name = tokens[i + 1]
    const eq = tokens[i + 2]
    const value = tokens[i + 3]
    if (!name || name.type !== 'ident') continue
    if (!eq || eq.type !== 'punct' || eq.value !== '=') continue

    // Only warn on config-like names. Local counters and scratch variables are
    // normal implementation detail and produce noisy warnings.
    const looksConfigLike =
      /^[A-Z][A-Z0-9_]*$/.test(name.value) ||
      /(timeout|limit|retries|retry|delay|interval|host|port|url|api|key|token|model|version|endpoint)$/i.test(name.value)
    if (!looksConfigLike) continue

    if (value?.type === 'number') {
      addFinding(findings, filename, 'hardcoded_value', start.line, `${name.value} = ${value.value} - consider making this a parameter`, 'warning')
    } else if (value?.type === 'string' && value.value.length > 0) {
      addFinding(findings, filename, 'hardcoded_value', start.line, `${name.value} = "${value.value.substring(0, 60)}" - consider making this a parameter`, 'warning')
    }
  }
}

function detectStringContamination(tokens, filename, findings, inTests, inExamples) {
  const isFixtureContext = inTests || inExamples
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i]
    if (tok.type === 'newline') continue
    if (tok.type === 'string' || tok.type === 'template') {
      const value = tok.value
      if (ABS_PATH_RE.test(value)) {
        addFinding(findings, filename, 'abs_path', tok.line, `absolute path "${value.substring(0, 60)}" - widgets must be portable`, 'block')
      }
      // hardcoded_url: src only - tests and examples legitimately use mock
      // URLs as fixtures (matches hardcoded_value precedent)
      if (!isFixtureContext && URL_RE.test(value)) {
        addFinding(findings, filename, 'hardcoded_url', tok.line, `hardcoded URL "${value.substring(0, 60)}"`, 'warning')
      }
      // hardcoded_ip: src only - tests and examples legitimately use mock IPs
      // as fixture data (matches hardcoded_url/hardcoded_value precedent).
      if (!isFixtureContext && IP_RE.test(value)) {
        // Skip single-digit-only patterns like "1.2.3.4" which are indistinguishable from version strings
        const octets = value.split(':')[0].split('.')
        if (octets.some(o => o.length >= 2)) {
          addFinding(findings, filename, 'hardcoded_ip', tok.line, `hardcoded IP "${value}"`, 'block')
        }
      }

      let assignmentHead = ''
      for (let j = Math.max(0, i - 4); j < i; j++) {
        const prev = tokens[j]
        if (prev.type === 'newline') continue
        assignmentHead += prev.value
      }
      if (CREDENTIAL_RE.test(assignmentHead)) {
        addFinding(
          findings,
          filename,
          'credential',
          tok.line,
          inTests
            ? `possible credential in test - verify it's fake: ${value.substring(0, 40)}`
            : `possible credential assignment - ${value.substring(0, 40)}`,
          inTests ? 'warning' : 'block'
        )
      }
    }
  }
}

function scanFile(filename) {
  const findings = []
  const content = fs.readFileSync(filename, 'utf-8')
  const tokens = tokenize(content)
  const inTests = filename.includes('/tests/') || filename.includes('\\tests\\')
  const inExamples = filename.includes('/examples/') || filename.includes('\\examples\\')

  if (!inExamples) {
    const consoleSeverity = inTests ? 'warning' : undefined
    const consoleFindings = []
    detectMemberCall(
      tokens,
      'console',
      new Set(['log', 'warn', 'error', 'debug', 'info', 'trace']),
      'console_log',
      member => inTests
        ? `console.${member}() in test - consider removing debug output`
        : `console.${member}() call - remove debug output from src/`,
      consoleFindings,
      filename
    )
    for (const f of consoleFindings) {
      if (consoleSeverity) f.severity = consoleSeverity
      findings.push(f)
    }
  }

  detectMemberCall(
    tokens,
    'process',
    new Set(['exit']),
    'process_exit',
    () => 'process.exit() call - widgets must not exit the process',
    findings,
    filename
  )

  detectMemberAccess(
    tokens,
    'process',
    new Set(['env']),
    'env_var',
    () => 'process.env access - verify it is not project-specific',
    findings,
    filename,
    'warning'
  )

  for (let i = 0; i < tokens.length - 1; i++) {
    if (tokens[i].type === 'ident' && tokens[i].value === 'eval' &&
        tokens[i + 1].type === 'punct' && tokens[i + 1].value === '(') {
      addFinding(findings, filename, 'eval', tokens[i].line, 'eval() call - dynamic code execution is a security risk')
    }
  }

  detectImports(tokens, filename, findings, inTests, inExamples)
  detectSleepCalls(tokens, filename, findings, inTests, inExamples)
  detectAssignments(tokens, filename, findings, inTests, inExamples)
  detectStringContamination(tokens, filename, findings, inTests, inExamples)

  return findings
}

if (process.argv.length < 3) {
  console.log(JSON.stringify({ error: 'usage: node js_scanner.js <file1> [file2 ...]' }))
  process.exit(1)
}

const allFindings = []
for (let i = 2; i < process.argv.length; i++) {
  const filename = process.argv[i]
  if (!fs.existsSync(filename)) {
    allFindings.push({ file: filename, kind: 'error', line: 0, detail: 'file not found' })
    continue
  }
  allFindings.push(...scanFile(filename))
}

console.log(JSON.stringify(allFindings))
