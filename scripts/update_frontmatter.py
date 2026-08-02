# update_frontmatter.py
# date created: 2026-05-17 13:57:07
# date modified: 2026-08-02 11:53:12
# tags: #frontmatter, #metadata, #headers, #update, #utility

import sys
import os
import re
import datetime

def main():
    if len(sys.argv) < 2:
        return
        
    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        return
        
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return

    # Get system dates
    ctime = os.path.getctime(filepath)
    mtime = os.path.getmtime(filepath)
    date_created = datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")
    date_modified = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    # Handle Python and PowerShell files with comment blocks
    if ext in ('.py', '.ps1'):
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
                            # Split by comma or whitespace only if not inside a tag path
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
                
                # Skip existing date and tag lines so they are cleanly replaced
                while i + 1 < len(lines) and lines[i+1].startswith('#') and ('date created:' in lines[i+1] or 'date modified:' in lines[i+1] or 'tags:' in lines[i+1]):
                    i += 1
                    
                header_updated = True
            i += 1
            
        if not header_updated:
            # Fallback if anchor is missing
            out_lines = [
                f"# {filename}",
                f"# date created: {date_created}",
                f"# date modified: {date_modified}",
                f"# tags: ",
                ""
            ] + lines
            
        new_content = '\n'.join(out_lines)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
        return
        
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
                # Inject missing keys before closing ---
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
                        # Preserve original creation date if it exists
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
        # Prepend frontmatter entirely
        fm = [
            "---",
            f"title: {filename}",
            f"date created: {date_created}",
            f"date modified: {date_modified}",
            "tags: ",
            "---",
            ""
        ]
        # Avoid double blank lines if content already starts with one
        if content.startswith('\n'):
            new_content = '\n'.join(fm) + content[1:]
        else:
            new_content = '\n'.join(fm) + content

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)

if __name__ == "__main__":
    main()
