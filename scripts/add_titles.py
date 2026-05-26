
# add_titles.py
# date created: 2026-05-17 13:41:00
# date modified: 2026-05-19 20:39:53
# tags: titles, header, frontmatter, automation, script

import os
import re

ROOT_DIR = r"C:\Projects\LocalAI"
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "venv", ".venv", ".vscode", "tmp"}

def process_py(filepath, filename):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return False

    if re.search(r'^#\s*' + re.escape(filename) + r'\s*$', content, re.MULTILINE):
        return False

    lines = content.split('\n')
    
    insert_idx = 0
    first_non_empty = 0
    while first_non_empty < len(lines) and lines[first_non_empty].strip() == '':
        first_non_empty += 1
        
    if first_non_empty < len(lines):
        first_line = lines[first_non_empty].strip()
        if first_line.startswith('"""') or first_line.startswith("'''"):
            quote_type = first_line[:3]
            if len(first_line) >= 6 and first_line.endswith(quote_type):
                insert_idx = first_non_empty + 1
            else:
                found_close = False
                for i in range(first_non_empty + 1, len(lines)):
                    if quote_type in lines[i]:
                        insert_idx = i + 1
                        found_close = True
                        break
                if not found_close:
                    insert_idx = first_non_empty
        else:
            insert_idx = first_non_empty
            
    # Insert `# filename.py`
    lines.insert(insert_idx, "")
    lines.insert(insert_idx + 1, f"# {filename}")
    
    # Only add trailing blank line if the next line isn't already blank
    if insert_idx + 2 < len(lines) and lines[insert_idx + 2].strip() != '':
        lines.insert(insert_idx + 2, "")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return True

def process_md_txt(filepath, filename):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return False
        
    lines = content.split('\n')
    if not lines:
        return False
        
    if re.search(r'^title:\s*' + re.escape(filename) + r'\s*$', content, re.MULTILINE):
        return False
        
    if content.startswith('---'):
        out_lines = []
        in_frontmatter = True
        inserted = False
        out_lines.append(lines[0])
        for line in lines[1:]:
            if in_frontmatter and line.strip() == '---':
                in_frontmatter = False
                out_lines.append(f"title: {filename}")
                inserted = True
            out_lines.append(line)
        if not inserted:
             out_lines = ['---', f'title: {filename}', '---', ''] + lines
    else:
        out_lines = ['---', f'title: {filename}', '---', ''] + lines
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines))
    return True

count = 0
for root, dirs, files in os.walk(ROOT_DIR):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
    
    for file in files:
        filepath = os.path.join(root, file)
        ext = os.path.splitext(file)[1].lower()
        
        try:
            if ext == '.py':
                if process_py(filepath, file):
                    print(f"Updated {filepath}")
                    count += 1
            elif ext in ['.md', '.txt']:
                if process_md_txt(filepath, file):
                    print(f"Updated {filepath}")
                    count += 1
        except Exception as e:
            print(f"Failed {filepath}: {e}")

print(f"Total files updated: {count}")
