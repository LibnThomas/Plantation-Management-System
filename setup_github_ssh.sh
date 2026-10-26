#!/usr/bin/env bash
# Sets up SSH for the GitHub account LibnThomas and points this project at it.
#   ./setup_github_ssh.sh
# - Uses a key you already have if it already logs in to GitHub as LibnThomas.
# - Otherwise makes a new key just for this account (an existing key of another
#   GitHub account is left alone - one key can belong to only one account).
set -uo pipefail
cd "$(dirname "$0")"

ACCOUNT="LibnThomas"
REPO="LibnThomas/Plantation-Management-System.git"
ALIAS="github-libn"
NEW_KEY="$HOME/.ssh/id_ed25519_libnthomas"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/config && chmod 600 ~/.ssh/config

github_user() {  # prints the GitHub account a key logs in as (empty if none)
  ssh -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=10 -i "$1" git@github.com 2>&1 | sed -n 's/^Hi \([^!]*\)!.*/\1/p'
}

KEY=""
echo "Looking for SSH keys in ~/.ssh ..."
for pub in ~/.ssh/*.pub; do
  [ -e "$pub" ] || continue
  key="${pub%.pub}"
  [ -f "$key" ] || continue
  user=$(github_user "$key")
  echo "  $(basename "$key"): ${user:-not linked to any GitHub account}"
  if [ "$user" = "$ACCOUNT" ]; then KEY="$key"; break; fi
done

if [ -n "$KEY" ]; then
  echo "Using your existing key: $KEY"
else
  if [ -f "$NEW_KEY" ]; then
    echo "Found $NEW_KEY, but it is not on GitHub yet."
  else
    echo "No key for $ACCOUNT - creating $NEW_KEY"
    ssh-keygen -t ed25519 -C "$ACCOUNT GitHub" -f "$NEW_KEY"
  fi
  KEY="$NEW_KEY"
  pbcopy < "$KEY.pub"
  echo
  echo "The public key is copied. In the browser (logged in as $ACCOUNT):"
  echo "  New SSH key > Title: this Mac > paste (Cmd+V) > Add SSH key"
  open "https://github.com/settings/ssh/new"
  read -r -p "Press Enter when it is added... " _
fi

ssh-add --apple-use-keychain "$KEY" 2>/dev/null || ssh-add "$KEY" 2>/dev/null || true

if ! grep -q "^Host $ALIAS\$" ~/.ssh/config; then
  cat >> ~/.ssh/config <<CFG

Host $ALIAS
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
  AddKeysToAgent yes
  UseKeychain yes
CFG
  echo "Added '$ALIAS' to ~/.ssh/config"
fi

result=$(ssh -T -o StrictHostKeyChecking=accept-new "git@$ALIAS" 2>&1)
if ! echo "$result" | grep -q "Hi $ACCOUNT!"; then
  echo "GitHub did not accept the key yet: $result"
  echo "Check the key is added under $ACCOUNT > Settings > SSH and GPG keys, then run this again."
  exit 1
fi
echo "SSH works: logged in to GitHub as $ACCOUNT"

if [ -d .git ]; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "git@$ALIAS:$REPO"
  else
    git remote add origin "git@$ALIAS:$REPO"
  fi
  echo "This project now pushes to git@$ALIAS:$REPO"
else
  echo "Run 'git init -b master' in this folder first, then run this script again to connect it."
fi
