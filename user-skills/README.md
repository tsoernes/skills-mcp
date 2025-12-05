# User Skills Directory

This directory contains user-created notes, assets, examples, and scripts that overlay onto Anthropic skills.

## Purpose

- **Track user content in git** while keeping Anthropic skills (in `skills/`) separate and gitignored
- **Extend Anthropic skills** with your own notes, examples, and learnings
- **Share knowledge** across your team by committing user notes to version control

## Structure

```
user-skills/
├── README.md (this file)
└── <skill-name>/
    ├── notes/           # User-created markdown notes
    │   └── *.md
    ├── _notes/          # Programmatic notes (via skill_store_note tool)
    │   └── *.md
    ├── examples/        # Code examples and reference implementations
    │   └── *.py, *.ts, etc.
    ├── scripts/         # Helper scripts and utilities
    │   └── *.py, *.sh, etc.
    └── assets/          # Any other supporting files
        └── *.*
```

## How It Works

When you fetch a skill (e.g., `mcp-builder`), the server automatically includes:

### Notes
1. **Skill's own notes** from `skills/skills/<skill-name>/_notes/` and `skills/skills/<skill-name>/notes/`
2. **User overlay notes** from `user-skills/<skill-name>/_notes/` and `user-skills/<skill-name>/notes/`

All notes are appended to the skill body under a "Notes" section, providing complete context.

### Assets
When listing or reading assets:
1. **Skill's own assets** from `skills/skills/<skill-name>/`
2. **User overlay assets** from `user-skills/<skill-name>/` (prefixed with `user-skills/<skill-name>/`)

Both are returned together, allowing you to organize examples, scripts, and other files separately.

## Creating User Notes

### Method 1: Manual Creation

Create markdown files directly:

```bash
mkdir -p user-skills/mcp-builder/notes
cat > user-skills/mcp-builder/notes/my-learnings.md << 'EOF'
---
title: "My Learnings About MCP Servers"
created_at: 2025-12-05T09:00:00Z
kind: note
---

## Overview
My notes about building MCP servers...

## Best Practices
- Always validate inputs
- Use structured outputs
- ...
EOF
```

### Method 2: Using the MCP Tool

The `skill_store_note` tool creates notes in the skill's `_notes/` directory. To create notes in user-skills instead, you'll need to manually move them or create them directly in `user-skills/<skill-name>/_notes/`.

## Note Format

Notes should include YAML frontmatter:

```markdown
---
title: "Your Note Title"
created_at: 2025-12-05T09:00:00Z
kind: note
---

## Your Content Here

Write your learnings, examples, and improvements...
```

## Git Tracking

This directory **is tracked by git** (unlike `skills/` which is gitignored), so:

- ✅ Commit your notes: `git add user-skills/ && git commit -m "Add notes for mcp-builder"`
- ✅ Push to share: `git push`
- ✅ Pull team updates: `git pull`

## Viewing Notes

Use the MCP tools:

```python
# List all notes for a skill (includes both skill and user notes)
skill_list_notes("mcp-builder", markdown_output=True)

# Get skill with all notes included (default)
skill_get_detail("mcp-builder", include_notes=True, markdown_output=True)

# List all assets for a skill (includes both skill and user assets)
skill_list_assets("mcp-builder", markdown_output=True)

# Read a user asset
skill_read_asset("mcp-builder", "user-skills/mcp-builder/examples/health_check_human_readable.py")
```

## Example Structure

```
user-skills/
├── README.md
├── mcp-builder/
│   ├── notes/
│   │   ├── health-check.md
│   │   └── smart-async.md
│   ├── examples/
│   │   ├── health_check_human_readable.py
│   │   └── smart_async_shielded_task.py
│   └── scripts/
│       ├── connections.py
│       ├── evaluation.py
│       └── requirements.txt
├── frontend-design/
│   ├── notes/
│   │   └── my-component-patterns.md
│   └── examples/
│       └── responsive-card.tsx
└── python-tools/
    ├── notes/
    │   └── debugging-tips.md
    └── _notes/
        └── 20251205T090000Z-automated-note.md
```

## Best Practices

1. **Document assets**: When adding code examples or reference files, create a note explaining what they are and when to use them
2. **Use descriptive titles**: Make notes easy to find and understand
3. **Include timestamps**: Use ISO 8601 format in frontmatter
4. **Be specific**: Focus on learnings, corrections, and practical examples
5. **Keep organized**: One skill per subdirectory

## Trash Policy

User notes can be moved to trash (not hard deleted) via:

```python
skill_trash_user_asset("mcp-builder", "user-skills/mcp-builder/notes/my-note.md")
```

Trashed items are moved to `trash/` directory and can be restored manually.

## Environment Variable

Override the default location:

```bash
export USER_SKILLS_DIR=/path/to/your/user-skills
```
