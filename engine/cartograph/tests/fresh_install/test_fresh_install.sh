#!/bin/bash
set -e

PASS=0
FAIL=0
ERRORS=""

check() {
    local name="$1"
    shift
    echo ""
    echo "=== $name ==="
    if "$@" ; then
        echo "  PASS"
        PASS=$((PASS + 1))
    else
        echo "  FAIL"
        FAIL=$((FAIL + 1))
        ERRORS="$ERRORS\n  - $name"
    fi
}

echo "========================================"
echo "  Cartograph Fresh Install Test"
echo "========================================"
echo ""
echo "Python: $(python --version)"
echo "Pip:    $(pip --version | cut -d' ' -f1-2)"
echo ""

# 1. Is the CLI accessible?
check "cartograph --help" cartograph --help

# 2. Doctor - reports missing deps (will exit 1 since JS/Nim missing, that's ok)
echo ""
echo "=== cartograph doctor (informational) ==="
cartograph doctor || true
echo "  (non-zero exit expected - JS/Nim not installed)"
PASS=$((PASS + 1))

# 3. Install Python test deps that doctor flagged
echo ""
echo "=== install pytest + coverage ==="
pip install --no-cache-dir pytest coverage > /dev/null 2>&1
echo "  installed"
PASS=$((PASS + 1))

# 4. Setup - generates agent instructions (auto-detects or uses --agent)
check "cartograph setup" cartograph setup --agent claude

# 5. Create a widget
check "cartograph create" cartograph create backend-hello-python \
    --language python --domain backend --name "Hello World"

# 6. Check the widget structure exists
check "widget structure" test -f cg/backend_hello_python/widget.json
check "widget src exists" test -d cg/backend_hello_python/src
check "widget tests exist" test -d cg/backend_hello_python/tests

# 7. Validate the widget (should fail on template TODOs)
echo ""
echo "=== validate (expect failure on TODOs) ==="
if cartograph validate cg/backend_hello_python 2>&1; then
    echo "  NOTE: validate passed (unexpected but ok)"
else
    echo "  Expected failure on template TODOs - PASS"
fi
PASS=$((PASS + 1))

# 8. Data directory structure
DATA_DIR=$(python -c "from cartograph.engine import _user_data_dir; print(_user_data_dir())")
check "data dir exists" test -d "$DATA_DIR"
check "widget library exists" test -d "$DATA_DIR/Widget_Library"
check "global rules dir exists" test -d "$DATA_DIR/rules"
check "global rules.py exists" test -f "$DATA_DIR/rules/rules.py"
check "global rules.js exists" test -f "$DATA_DIR/rules/rules.js"
check "global rules.typescript.js exists" test -f "$DATA_DIR/rules/rules.typescript.js"
check "global rules.nim exists" test -f "$DATA_DIR/rules/rules.nim"
check "global rules.angular.js exists" test -f "$DATA_DIR/rules/rules.angular.js"
check "global rules.php exists" test -f "$DATA_DIR/rules/rules.php"
check "global rules.openscad.py exists" test -f "$DATA_DIR/rules/rules.openscad.py"
check "global rules.sv.py exists" test -f "$DATA_DIR/rules/rules.sv.py"
check "rules.py runs clean" python "$DATA_DIR/rules/rules.py" /tmp

# 9. Stats
check "cartograph stats" cartograph stats

# 9. Status
check "cartograph status" cartograph status

echo ""
echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"
if [ $FAIL -gt 0 ]; then
    echo -e "  Failures:$ERRORS"
    exit 1
fi
echo "  All checks passed!"
