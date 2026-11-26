#!/usr/bin/env python3
"""
Update your GitHub profile from the command line.

Updates profile fields (name, bio, company, location, website, Twitter/X,
hireable) via PATCH /user, and optionally your profile README
(the special <username>/<username> repository).

Also tracks your daily push streak and embeds it into your README.

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
  python update_github_profile.py --streak               # update streak then push README
  python update_github_profile.py --streak --readme README.md

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

Streak state is stored in streak.json (auto-created next to this script).
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

API = "https://api.github.com"
PROFILE_FIELDS = ["name", "bio", "company", "location", "blog", "twitter_username", "hireable"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STREAK_FILE = os.path.join(SCRIPT_DIR, "streak.json")

# Markers used to inject the streak block into README.md
STREAK_START = "<!-- STREAK:START -->"
STREAK_END   = "<!-- STREAK:END -->"


# ─── HTTP helper ────────────────────────────────────────────────────────────

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


# ─── Profile & README helpers ────────────────────────────────────────────────

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


# ─── Streak logic ────────────────────────────────────────────────────────────

def load_streak():
    """Load streak state from streak.json, or return defaults."""
    if os.path.exists(STREAK_FILE):
        with open(STREAK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_streak": 0,
        "longest_streak": 0,
        "last_push_date": None,
        "total_days": 0,
        "streak_start_date": None,
    }


def save_streak(state):
    with open(STREAK_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    print(f"Streak saved to {STREAK_FILE}")


def compute_streak_from_github(token, username):
    """
    Fetch the user's push events from the GitHub Events API and
    compute an accurate streak from real push dates (up to 90 days / 3 pages).
    Returns an updated streak state dict.
    """
    print(f"Fetching push history for {username} from GitHub Events API…")
    push_dates = set()

    for page in range(1, 4):   # GitHub returns max 300 events across 3 pages
        url = f"{API}/users/{username}/events?per_page=100&page={page}"
        try:
            events = request("GET", url, token)
        except SystemExit:
            break
        if not events:
            break
        for ev in events:
            if ev.get("type") == "PushEvent":
                raw = ev.get("created_at", "")
                try:
                    d = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").date()
                    push_dates.add(d)
                except ValueError:
                    pass

    if not push_dates:
        print("No PushEvents found in recent history.")
        # Fall back to what's already stored
        return load_streak()

    today = date.today()
    sorted_dates = sorted(push_dates, reverse=True)

    # Build streaks
    current_streak = 0
    longest_streak = 0
    streak_start = None
    temp_streak = 1
    temp_start = sorted_dates[0]

    # Count current streak (working backward from today)
    cursor = today
    for d in sorted_dates:
        if d == cursor:
            current_streak += 1
            cursor -= __import__("datetime").timedelta(days=1)
        elif d < cursor:
            # Gap: check if we started the streak today or yesterday
            break

    # Recalculate longest streak across all recorded push dates
    if len(sorted_dates) == 1:
        temp_streak = 1
        longest_streak = 1
        streak_start = sorted_dates[0]
    else:
        for i in range(1, len(sorted_dates)):
            delta = (sorted_dates[i - 1] - sorted_dates[i]).days
            if delta == 1:
                temp_streak += 1
            else:
                if temp_streak > longest_streak:
                    longest_streak = temp_streak
                    streak_start = temp_start
                temp_streak = 1
                temp_start = sorted_dates[i]
        if temp_streak > longest_streak:
            longest_streak = temp_streak
            streak_start = temp_start

    state = {
        "current_streak": current_streak,
        "longest_streak": max(longest_streak, current_streak),
        "last_push_date": sorted_dates[0].isoformat(),
        "total_days": len(push_dates),
        "streak_start_date": streak_start.isoformat() if streak_start else None,
    }

    print(f"  Current streak : {state['current_streak']} day(s)")
    print(f"  Longest streak : {state['longest_streak']} day(s)")
    print(f"  Last push      : {state['last_push_date']}")
    print(f"  Total push days: {state['total_days']}")
    return state


def bump_streak_local():
    """
    Record today as a push day using only the local streak.json file.
    Use this when you don't want to hit the GitHub API.
    """
    state = load_streak()
    today = date.today()
    today_str = today.isoformat()

    last = state.get("last_push_date")
    if last == today_str:
        print("Streak already updated for today.")
        return state

    if last:
        last_date = date.fromisoformat(last)
        delta = (today - last_date).days
        if delta == 1:
            state["current_streak"] += 1
        elif delta > 1:
            print(f"  Streak broken! ({delta} day gap). Resetting to 1.")
            state["current_streak"] = 1
            state["streak_start_date"] = today_str
    else:
        state["current_streak"] = 1
        state["streak_start_date"] = today_str

    state["longest_streak"] = max(state["longest_streak"], state["current_streak"])
    state["last_push_date"] = today_str
    state["total_days"] = state.get("total_days", 0) + 1

    print(f"  Current streak : {state['current_streak']} day(s)")
    print(f"  Longest streak : {state['longest_streak']} day(s)")
    print(f"  Total push days: {state['total_days']}")
    return state


def build_streak_block(state):
    """Return the markdown block to inject between the STREAK markers."""
    current  = state.get("current_streak", 0)
    longest  = state.get("longest_streak", 0)
    total    = state.get("total_days", 0)
    last     = state.get("last_push_date", "—")

    # ── Flame / milestone scale ───────────────────────────────────────────────
    # < 1 day  → frozen (no streak)
    # 1–9 days → warming up 🔥
    # 10–29    → ON FIRE 🔥🔥  (milestone: fire introduced at day 10)
    # 30+      → LEGEND 🔥🔥🔥
    if current == 0:
        flame = "❄️"
        milestone = ""
    elif current < 10:
        flame = "🔥"
        milestone = ""
    elif current < 30:
        flame = "🔥🔥"
        milestone = "\n\n🔥 **You're on fire! 10+ day streak achieved!** 🔥"
    else:
        flame = "🔥🔥🔥"
        milestone = "\n\n🔥🔥🔥 **LEGENDARY streak! 30+ days of consistent pushing!** 🔥🔥🔥"

    lines = [
        "## 🔥 Push Streak",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| {flame} Current streak | **{current} day{'s' if current != 1 else ''}** |",
        f"| 🏆 Longest streak | **{longest} day{'s' if longest != 1 else ''}** |",
        f"| 📅 Total push days | **{total}** |",
        f"| 🕐 Last push | **{last}** |",
    ]

    if milestone:
        lines.append(milestone)

    lines += [
        "",
        f"> Automatically updated by [update_github_profile.py](./update_github_profile.py)",
    ]
    return "\n".join(lines)


def inject_streak_into_readme(readme_path, state):
    """
    Replace the content between STREAK:START / STREAK:END markers in the README.
    If the markers don't exist, append the streak block at the end.
    """
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    block = build_streak_block(state)
    replacement = f"{STREAK_START}\n{block}\n{STREAK_END}"

    pattern = re.compile(
        rf"{re.escape(STREAK_START)}.*?{re.escape(STREAK_END)}",
        re.DOTALL,
    )

    if STREAK_START in content:
        updated = pattern.sub(replacement, content)
    else:
        updated = content.rstrip() + "\n\n" + replacement + "\n"

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"Streak block injected into {readme_path}")


# ─── Main ────────────────────────────────────────────────────────────────────

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

    # Streak options
    streak_group = p.add_mutually_exclusive_group()
    streak_group.add_argument(
        "--streak",
        action="store_true",
        help="Fetch push history from GitHub, update streak.json, and inject into README",
    )
    streak_group.add_argument(
        "--streak-local",
        action="store_true",
        help="Bump the streak using only the local streak.json (no GitHub API call)",
    )
    p.add_argument(
        "--streak-readme",
        default="README.md",
        metavar="PATH",
        help="README to inject the streak block into (default: README.md next to this script)",
    )

    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set the GITHUB_TOKEN environment variable first (see the top of this script).")

    # ── Streak update ────────────────────────────────────────────────────────
    if args.streak or args.streak_local:
        readme_for_streak = os.path.join(SCRIPT_DIR, args.streak_readme)

        if args.streak:
            me = request("GET", "/user", token)
            username = me["login"]
            state = compute_streak_from_github(token, username)
        else:
            state = bump_streak_local()

        save_streak(state)

        if os.path.exists(readme_for_streak):
            inject_streak_into_readme(readme_for_streak, state)
        else:
            print(f"README not found at {readme_for_streak}; skipping injection.")

        # If --readme was also passed, push the updated README to GitHub via API.
        # NOTE: When running in GitHub Actions, skip this — the workflow's git
        # commit/push step already handles it, and the GITHUB_TOKEN cannot write
        # to a different repo via the Contents API.
        if args.readme and not os.environ.get("GITHUB_ACTIONS"):
            me = request("GET", "/user", token)
            update_readme(token, me["login"], readme_for_streak, args.dry_run)
        elif args.streak and not os.environ.get("GITHUB_ACTIONS"):
            # Auto-push when --streak is used outside of GitHub Actions
            update_readme(token, username, readme_for_streak, args.dry_run)

        return   # streak-only run; skip profile field update below

    # ── Profile fields update ────────────────────────────────────────────────
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
        p.error("Nothing to update. Pass profile fields, --config, --readme, --streak, or --streak-local.")

    update_profile(token, fields, args.dry_run)

    if args.readme:
        me = request("GET", "/user", token)
        update_readme(token, me["login"], args.readme, args.dry_run)


if __name__ == "__main__":
    main()
