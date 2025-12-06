#!/bin/bash
set -e # Stop immediately on error (Fail fast)

# Add some colors for dramatic effect
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}🚀 Starting Integration Test for PyVegh...${NC}"

# 1. Setup test environment
TEST_DIR="test_sandbox"
SRC_DIR="$TEST_DIR/source"
RESTORE_DIR="$TEST_DIR/restored"
SNAP_FILE="$TEST_DIR/backup.vegh"

# Cleanup old remnants
rm -rf $TEST_DIR
mkdir -p $SRC_DIR

# Generate dummy data (Text, Binary, Deep Nested)
echo -e "${CYAN}🛠️  Generating dummy data...${NC}"
echo "Hello CodeTease" > "$SRC_DIR/hello.txt"
echo "Rust is fast" > "$SRC_DIR/rust.rs"
mkdir -p "$SRC_DIR/nested/deep"
echo "Secrets hidden here" > "$SRC_DIR/nested/deep/secret.txt"
# Create a fake binary file (1MB)
dd if=/dev/urandom of="$SRC_DIR/random.bin" bs=1M count=1 2>/dev/null

# 2. Test SNAP command
echo -e "${CYAN}📸 Test 1: vegh snap...${NC}"
# Snap the CONTENT of SRC_DIR
vegh snap "$SRC_DIR" --output "$SNAP_FILE" --comment "CI Test Run"

if [ -f "$SNAP_FILE" ]; then
    echo -e "${GREEN}✔ Snap file created!${NC}"
else
    echo -e "${RED}✘ Snap file missing!${NC}"
    exit 1
fi

# 3. Test LIST command
echo -e "${CYAN}📜 Test 2: vegh list...${NC}"
LIST_OUTPUT=$(vegh list "$SNAP_FILE" --flat)
if echo "$LIST_OUTPUT" | grep -q "random.bin"; then
    echo -e "${GREEN}✔ File list contains random.bin${NC}"
else
    echo -e "${RED}✘ File list is missing content!${NC}"
    exit 1
fi

# 4. Test CHECK command
echo -e "${CYAN}✅ Test 3: vegh check...${NC}"
if vegh check "$SNAP_FILE" | grep -q "Valid Snapshot"; then
    echo -e "${GREEN}✔ Integrity check passed${NC}"
else
    echo -e "${RED}✘ Integrity check failed!${NC}"
    exit 1
fi

# 5. Test RESTORE command
echo -e "${CYAN}📦 Test 4: vegh restore...${NC}"
# Restore directly to RESTORE_DIR
vegh restore "$SNAP_FILE" "$RESTORE_DIR"

# 6. Compare content (Diff)
echo -e "${CYAN}🔍 Test 5: Comparing source vs restored...${NC}"

# FIX: Compare the directories directly.
# NOTE: We exclude '.veghcache' because it is created in source during runtime (Format V2)
# but explicitly ignored/stripped from the snapshot.
diff -r --exclude=".veghcache" --exclude="__pycache__" --exclude=".DS_Store" "$SRC_DIR" "$RESTORE_DIR"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✔ Source and restored data MATCH 100%!${NC}"
else
    echo -e "${RED}✘ Data mismatch detected!${NC}"
    # Show what's in there for debugging
    echo "Content of restored dir:"
    ls -R "$RESTORE_DIR"
    exit 1
fi

# 7. Test LOC command
echo -e "${CYAN}📊 Test 6: vegh loc...${NC}"
vegh loc "$SNAP_FILE" --raw > /dev/null
echo -e "${GREEN}✔ LOC command runs successfully${NC}"

# Cleanup
echo -e "${CYAN}🧹 Cleaning up...${NC}"
rm -rf $TEST_DIR

echo -e "${GREEN}🎉🎉🎉 ALL TESTS PASSED! 🎉🎉🎉${NC}"