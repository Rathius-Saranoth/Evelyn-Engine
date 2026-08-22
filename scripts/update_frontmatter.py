#!/usr/bin/env python3
# update_frontmatter.py
# date created: 2026-05-17 13:57:07
# date modified: 2026-08-21 21:04:07
# tags: #frontmatter, #metadata, #headers, #update, #utility

import sys
import os
import re
import datetime

# Explicitly supported file extensions
YAML_EXTENSIONS = {'.md', '.markdown', '.txt'}
COMMENT_EXTENSIONS = {'.py', '.ps1'}
SUPPORTED_EXTENSIONS = YAML_EXTENSIONS | COMMENT_EXTENSIONS

# Explicitly ignored / unsupported extensions
UNSUPPORTED_EXTENSIONS = {
    '.html', '.htm', '.xml', '.xhtml', '.json', '.css', '.js', '.ts',
    '.jsx', '.tsx', '.svg', '.yaml', '.yml', '.toml', '.ini', '.cfg',
    '.sql', '.db', '.sqlite', '.log', '.csv', '.tsv', '.png', '.jpg',
    '.jpeg', '.gif', '.webp', '.ico', '.wav', '.mp3', '.mp4', '.zip',
    '.tar', '.gz', '.bin', '.bak', '.lock'
}

def update_file_frontmatter(filepath: str) -> bool:
    """Updates the frontmatter or header comment of a single file if supported.
    
    Returns:
        bool: True if file was updated, False otherwise.
    """
    filepath = os.path.normpath(filepath)
    if not os.path.exists(filepath) or os.path.isdir(filepath):
        return False

    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        # Unsupported file type (e.g. .xml, .html, .json, .css, etc.) - do not touch
        return False

    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            content = f.read()
    except UnicodeDecodeError:
        return False

    # Get system dates
    st = os.stat(filepath)
    ctime = getattr(st, 'st_birthtime', st.st_ctime)
    mtime = st.st_mtime
    date_created = datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")
    date_modified = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    # Handle Python and PowerShell files with comment blocks
    if ext in COMMENT_EXTENSIONS:
        lines = content.split('\n')
        out_lines = []
        i = 0
        header_updated = False

        while i < len(lines):
            line = lines[i]
            out_lines.append(line)

            if not header_updated and re.match(r'^#\s*' + re.escape(filename) + r'\s*$', line):
                created_val = date_created
                existing_tags_line = None
                j = i + 1
                while j < len(lines) and lines[j].startswith('#'):
                    if 'date created:' in lines[j]:
                        m = re.search(r'date created:\s*(.*)', lines[j])
                        if m and m.group(1).strip():
                            created_val = m.group(1).strip()
                    elif 'tags:' in lines[j]:
                        existing_tags_line = lines[j]
                    j += 1

                out_lines.append(f"# date created: {created_val}")
                out_lines.append(f"# date modified: {date_modified}")

                if existing_tags_line is not None:
                    m = re.search(r'tags:\s*(.*)$', existing_tags_line)
                    if m:
                        tags_content = m.group(1).strip()
                        if tags_content:
                            if ',' in tags_content:
                                raw_tags = [t.strip() for t in tags_content.split(',') if t.strip()]
                            else:
                                raw_tags = [t.strip() for t in tags_content.split() if t.strip()]
                            formatted = []
                            for t in raw_tags:
                                if t.startswith('#'):
                                    formatted.append(t)
                                else:
                                    formatted.append(f"#{t}")
                            out_lines.append(f"# tags: {', '.join(formatted)}")
                        else:
                            out_lines.append("# tags: ")
                    else:
                        out_lines.append(existing_tags_line)
                else:
                    out_lines.append("# tags: ")

                while i + 1 < len(lines) and lines[i+1].startswith('#') and (
                    'date created:' in lines[i+1] or 'date modified:' in lines[i+1] or 'tags:' in lines[i+1]
                ):
                    i += 1

                header_updated = True
            i += 1

        if not header_updated:
            header = [
                f"# {filename}",
                f"# date created: {date_created}",
                f"# date modified: {date_modified}",
                f"# tags: ",
                ""
            ]
            if lines and lines[0].startswith('#!'):
                out_lines = [lines[0]] + header + lines[1:]
            else:
                out_lines = header + lines

        new_content = '\n'.join(out_lines)

        if new_content != content:
            with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_content)
            return True
        return False

    # Handle YAML frontmatter for .md, .markdown, .txt
    lines = content.split('\n')
    new_content = ""
    if content.startswith('---'):
        in_fm = True
        out_lines = []
        out_lines.append(lines[0])
        fm_keys_found = set()

        for line in lines[1:]:
            if in_fm and line.strip() == '---':
                in_fm = False
                if 'title' not in fm_keys_found:
                    out_lines.append(f"title: {filename}")
                if 'date created' not in fm_keys_found:
                    out_lines.append(f"date created: {date_created}")
                if 'date modified' not in fm_keys_found:
                    out_lines.append(f"date modified: {date_modified}")
                if 'tags' not in fm_keys_found:
                    out_lines.append("tags: ")
                out_lines.append(line)
                continue

            if in_fm:
                match = re.match(r'^([^:]+):\s*(.*)$', line)
                if match:
                    key = match.group(1).strip()
                    fm_keys_found.add(key)

                    if key == 'title':
                        out_lines.append(f"title: {filename}")
                    elif key == 'date modified':
                        out_lines.append(f"date modified: {date_modified}")
                    elif key == 'date created':
                        existing_val = match.group(2).strip()
                        if not existing_val:
                            out_lines.append(f"date created: {date_created}")
                        else:
                            out_lines.append(line)
                    else:
                        out_lines.append(line)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)

        new_content = '\n'.join(out_lines)
    else:
        fm = [
            "---",
            f"title: {filename}",
            f"date created: {date_created}",
            f"date modified: {date_modified}",
            "tags: ",
            "---",
            ""
        ]
        if content.startswith('\n'):
            new_content = '\n'.join(fm) + content[1:]
        else:
            new_content = '\n'.join(fm) + content

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_content)
        return True
    return False

def main():
    if len(sys.argv) < 2:
        return
    for arg in sys.argv[1:]:
        update_file_frontmatter(arg)

if __name__ == "__main__":
    main()
