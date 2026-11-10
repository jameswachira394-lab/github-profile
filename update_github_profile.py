#!/usr/bin/env python3
"""
Update your GitHub profile from the command line.

Updates profile fields (name, bio, company, location, website, Twitter/X,
hireable) via PATCH /user, and optionally your profile README
(the special <username>/<username> repository).

Setup:
  1. Create a personal access token at https://github.com/settings/tokens
     - Classic token: "user" scope (and "repo" if updating the README)
     - Fine-grained token: "Profile" write (and "Contents" write on the
       <username>/<username> repo for the README)
  2. export GITHUB_TOKEN=ghp_xxxxxxxxxxxx

Usage:
  python update_github_profile.py --bio "Backend dev. Coffee lover." --location "Nairobi, Kenya"
  python update_github_profile.py --name "Jane Doe" --company "@acme" --blog "https://jane.dev"
  python update_github_profile.py --readme README.md
  python update_github_profile.py --config profile.json
  python update_github_profile.py --bio "New bio" --dry-run

profile.json example:
  {
    "name": "Jane Doe",
    "bio": "Backend dev. Coffee lover.",
    "company": "@acme",
    "location": "Nairobi, Kenya",
    "blog": "https://jane.dev",
    "twitter_username": "janedoe",
    "hireable": true
  }
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
PROFILE_FIELDS = ["name", "bio", "company", "location", "blog", "twitter_username", "hireable"]


def request(method, path, token, body=None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "profile-updater-script")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"GitHub API error {e.code} on {method} {path}: {detail}")


def update_profile(token, fields, dry_run):
    if not fields:
        return
    print("Profile changes:")
    for k, v in fields.items():
        print(f"  {k}: {v!r}")
    if dry_run:
        print("(dry run, nothing sent)")
        return
    result = request("PATCH", "/user", token, fields)
    print(f"Profile updated: {result.get('html_url')}")


def update_readme(token, username, readme_path, dry_run):
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    repo_path = f"/repos/{username}/{username}/contents/README.md"
    print(f"README: updating {username}/{username}/README.md from {readme_path}")
    if dry_run:
        print("(dry run, nothing sent)")
        return

    # Fetch current SHA if the file already exists
    sha = None
    try:
        existing = request("GET", repo_path, token)
        sha = existing.get("sha")
    except SystemExit:
        print("README not found (or repo missing); attempting to create it.")
        print(f"Make sure the repo github.com/{username}/{username} exists and is public.")

    body = {
        "message": "Update profile README",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        body["sha"] = sha
    request("PUT", repo_path, token, body)
    print(f"README updated: https://github.com/{username}")


def main():
    p = argparse.ArgumentParser(description="Update your GitHub profile.")
    p.add_argument("--name")
    p.add_argument("--bio")
    p.add_argument("--company")
    p.add_argument("--location")
    p.add_argument("--blog", help="Website URL")
    p.add_argument("--twitter", dest="twitter_username", help="Twitter/X username (no @)")
    p.add_argument("--hireable", choices=["true", "false"])
    p.add_argument("--config", help="JSON file with profile fields")
    p.add_argument("--readme", help="Markdown file to publish as your profile README")
    p.add_argument("--dry-run", action="store_true", help="Show changes without applying them")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set the GITHUB_TOKEN environment variable first (see the top of this script).")

    fields = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        fields.update({k: cfg[k] for k in PROFILE_FIELDS if k in cfg})

    for k in PROFILE_FIELDS:
        v = getattr(args, k, None)
        if v is not None:
            fields[k] = (v == "true") if k == "hireable" else v

    if not fields and not args.readme:
        p.error("Nothing to update. Pass profile fields, --config, or --readme.")

    update_profile(token, fields, args.dry_run)

    if args.readme:
        me = request("GET", "/user", token)
        update_readme(token, me["login"], args.readme, args.dry_run)


if __name__ == "__main__":
    main()
