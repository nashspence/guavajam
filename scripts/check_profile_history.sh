#!/bin/sh
# Compare the checked-out tree with the PR base or the previous push tip.
set -eu
base=$1
if [ -z "$base" ] || [ "$base" = 0000000000000000000000000000000000000000 ]; then
    # New refs and manual checks have no previous tip.
    base=$(git merge-base HEAD "refs/remotes/origin/$2")
elif ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    # A force-push can leave the previous tip outside the fetched history.
    git fetch --no-tags origin "$base"
fi

# Report changes to existing paths, including the deletion half of a rename.
git diff --exit-code --name-status --no-renames --diff-filter=a "$base" HEAD -- \
    ':(top,glob)templates/edition-v[0-9]*.html' ':(top,glob)styles/edition-v[0-9]*.css'
