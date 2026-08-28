# vault_list_manager.py
# date created: 2026-08-23
# date modified: 2026-08-28 11:51:25
# tags: #obsidian, #vault, #lists, #groceries, #checklists, #tools

"""vault_list_manager.py — Local Obsidian Vault List and Checklist Manager.

Provides structured parsing, category-aware section routing, quantity incrementing,
and checklist management directly on markdown files in the user's Obsidian Vault.
"""

from __future__ import annotations

import datetime
import os
import re
from typing import Any, Optional

import evelyn_config as cfg
from Evelyn.tools.tag_librarian import format_yaml_array


def get_lists_directory() -> str:
    """Get the configured lists directory path, ensuring it exists."""
    lists_dir = getattr(cfg, "LISTS_DIR", os.path.join(cfg.VAULT_BASE_DIR, "Lists"))
    os.makedirs(lists_dir, exist_ok=True)
    return lists_dir


def normalize_list_name(name: str) -> str:
    """Normalize list name to a clean title and filename."""
    clean = name.strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3].strip()
    return clean


def get_list_path(name: str) -> str:
    """Return the absolute filepath for a given list name."""
    clean = normalize_list_name(name)
    return os.path.join(get_lists_directory(), f"{clean}.md")


def list_all_lists() -> list[str]:
    """Return a list of all existing list names in the vault lists directory."""
    lists_dir = get_lists_directory()
    if not os.path.exists(lists_dir):
        return []
    names = []
    for f in sorted(os.listdir(lists_dir)):
        if f.endswith(".md") and not f.startswith("."):
            names.append(f[:-3])
    return names


def ensure_list_exists(name: str) -> str:
    """Ensure the markdown list file exists, creating it from a template if needed.

    Args:
        name: Name of the list (e.g. "Groceries", "Packing").

    Returns:
        str: Absolute filepath of the list.
    """
    path = get_list_path(name)
    if os.path.exists(path):
        return path

    clean_name = normalize_list_name(name)
    slug = clean_name.lower().replace(" ", "_")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check for specific template in templates/lists/
    specific_tmpl = os.path.join(cfg.BASE_DIR, "templates", "lists", f"{slug}.md")
    generic_tmpl = os.path.join(cfg.BASE_DIR, "templates", "list_template.md")

    template_content = ""
    if os.path.exists(specific_tmpl):
        with open(specific_tmpl, "r", encoding="utf-8") as f:
            template_content = f.read()
    elif os.path.exists(generic_tmpl):
        with open(generic_tmpl, "r", encoding="utf-8") as f:
            template_content = f.read()
    else:
        tags_str = format_yaml_array(["list", slug])
        template_content = (
            "---\n"
            f"title: {clean_name}\n"
            f"tags: {tags_str}\n"
            f"date created: {now_str}\n"
            f"date modified: {now_str}\n"
            "---\n"
            f"# 📋 {clean_name}\n\n"
            "## Items\n"
        )

    # Fill placeholders
    rendered = template_content.replace("{{TITLE}}", clean_name).replace("{{SLUG}}", slug).replace("{{DATE}}", now_str)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(rendered)

    return path


def format_item_line(name: str, quantity: Optional[float | int | str] = None, unit: Optional[str] = None) -> str:
    """Format an item string into scannable 'Item (Qty Unit)' presentation.

    Args:
        name: Clean item name.
        quantity: Optional count or amount.
        unit: Optional unit string.

    Returns:
        str: Formatted checklist string (e.g. '- [ ] Whole Milk (1 gal)').
    """
    clean_name = name.strip()
    qty_str = ""
    if quantity is not None and str(quantity).strip():
        q_val = str(quantity).strip()
        # Format whole floats cleanly (1.0 -> 1)
        try:
            f_val = float(q_val)
            if f_val.is_integer():
                q_val = str(int(f_val))
        except ValueError:
            pass

        u_val = str(unit).strip() if unit else ""
        if u_val:
            qty_str = f" ({q_val} {u_val})"
        else:
            qty_str = f" ({q_val}x)" if q_val != "1" else ""
    elif unit and str(unit).strip():
        qty_str = f" ({str(unit).strip()})"

    return f"- [ ] {clean_name}{qty_str}"


def parse_item_line(line: str) -> Optional[dict[str, Any]]:
    """Parse a markdown checklist item line into structured fields.

    Matches lines like:
      - [ ] Whole Milk (1 gal)
      - [x] Greek Yogurt (2x)
      - [ ] Olive Oil

    Returns:
        dict with keys: 'raw', 'status', 'name', 'quantity', 'unit', or None if not a checklist line.
    """
    match = re.match(r"^(\s*[-*]\s*\[([ xX])\])\s+(.+)$", line)
    if not match:
        return None

    status = "completed" if match.group(2).lower() == "x" else "pending"
    text = match.group(3).strip()

    # Match name and optional (qty unit) or (unit) at end
    parenthesis_match = re.match(r"^(.*?)\s*\((.+?)\)$", text)
    if parenthesis_match:
        name = parenthesis_match.group(1).strip()
        inside = parenthesis_match.group(2).strip()

        # Check if inside is "2x" or "2"
        x_match = re.match(r"^(\d+(?:\.\d+)?)\s*[xX]$", inside)
        if x_match:
            return {
                "raw": line,
                "status": status,
                "name": name,
                "quantity": float(x_match.group(1)) if "." in x_match.group(1) else int(x_match.group(1)),
                "unit": "",
            }

        # Check if inside is "1 gal" or "2 boxes"
        qty_unit_match = re.match(r"^(\d+(?:\.\d+)?)\s+(.+)$", inside)
        if qty_unit_match:
            q_str = qty_unit_match.group(1)
            u_str = qty_unit_match.group(2).strip()
            return {
                "raw": line,
                "status": status,
                "name": name,
                "quantity": float(q_str) if "." in q_str else int(q_str),
                "unit": u_str,
            }

        # Otherwise treat inside as unit or details
        return {
            "raw": line,
            "status": status,
            "name": name,
            "quantity": 1,
            "unit": inside,
        }

    return {
        "raw": line,
        "status": status,
        "name": text,
        "quantity": None,
        "unit": None,
    }


def parse_list_file(filepath: str) -> dict[str, Any]:
    """Parse a markdown list document into structured sections and metadata."""
    if not os.path.exists(filepath):
        return {"frontmatter": "", "title": "", "sections": {}, "order": []}

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    frontmatter_lines: list[str] = []
    in_frontmatter = False
    doc_lines: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            frontmatter_lines.append(line)
            i += 1
            while i < len(lines):
                fm_line = lines[i]
                frontmatter_lines.append(fm_line)
                if fm_line.strip() == "---":
                    in_frontmatter = False
                    i += 1
                    break
                i += 1
            continue

        doc_lines.append(line)
        i += 1

    sections: dict[str, list[dict[str, Any]]] = {}
    section_order: list[str] = []
    current_section = "Items"
    title = ""

    for line in doc_lines:
        s_line = line.strip()
        # Title heading (# Title)
        if s_line.startswith("# ") and not title:
            title = s_line[2:].strip()
            continue

        # Section heading (## Category)
        if s_line.startswith("## "):
            current_section = s_line[3:].strip()
            if current_section not in sections:
                sections[current_section] = []
                section_order.append(current_section)
            continue

        # Parse item line
        item_data = parse_item_line(line)
        if item_data:
            if current_section not in sections:
                sections[current_section] = []
                section_order.append(current_section)
            sections[current_section].append(item_data)

    return {
        "frontmatter": "".join(frontmatter_lines),
        "title": title or os.path.basename(filepath)[:-3],
        "sections": sections,
        "order": section_order,
    }


def write_list_file(filepath: str, parsed: dict[str, Any]) -> None:
    """Serialize structured sections back to markdown document on disk."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    frontmatter = parsed.get("frontmatter", "")
    if frontmatter:
        # Update date modified
        if "date modified:" in frontmatter:
            frontmatter = re.sub(r"date modified:.*", f"date modified: {now_str}", frontmatter)
        else:
            frontmatter = frontmatter.rstrip()
            if frontmatter.endswith("---"):
                frontmatter = frontmatter[:-3].rstrip() + f"\ndate modified: {now_str}\n---\n"
    else:
        title = parsed.get("title", "List")
        slug = title.lower().replace(" ", "_")
        tags_str = format_yaml_array(["list", slug])
        frontmatter = (
            "---\n"
            f"title: {title}\n"
            f"tags: {tags_str}\n"
            f"date created: {now_str}\n"
            f"date modified: {now_str}\n"
            "---\n"
        )

    lines: list[str] = []
    if frontmatter:
        lines.append(frontmatter.strip() + "\n\n")

    title = parsed.get("title", "List")
    if not title.startswith("#"):
        lines.append(f"# {title}\n\n")
    else:
        lines.append(f"{title}\n\n")

    sections = parsed.get("sections", {})
    order = parsed.get("order", list(sections.keys()))

    # Ensure all existing sections in order are rendered
    rendered_sections = set()
    for sec in order:
        if sec in rendered_sections:
            continue
        rendered_sections.add(sec)
        items = sections.get(sec, [])
        lines.append(f"## {sec}\n")
        for item in items:
            name = item.get("name", "")
            qty = item.get("quantity")
            unit = item.get("unit")
            status = item.get("status", "pending")
            check_box = "- [x]" if status == "completed" else "- [ ]"
            formatted = format_item_line(name, qty, unit)
            # Replace "- [ ]" with check_box
            final_line = re.sub(r"^-\s*\[\s*\]", check_box, formatted)
            lines.append(f"{final_line}\n")
        lines.append("\n")

    # Any remaining sections not in order
    for sec, items in sections.items():
        if sec in rendered_sections:
            continue
        rendered_sections.add(sec)
        lines.append(f"## {sec}\n")
        for item in items:
            name = item.get("name", "")
            qty = item.get("quantity")
            unit = item.get("unit")
            status = item.get("status", "pending")
            check_box = "- [x]" if status == "completed" else "- [ ]"
            formatted = format_item_line(name, qty, unit)
            final_line = re.sub(r"^-\s*\[\s*\]", check_box, formatted)
            lines.append(f"{final_line}\n")
        lines.append("\n")

    content = "".join(lines).rstrip() + "\n"
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def find_matching_section(parsed: dict[str, Any], category: Optional[str] = None) -> str:
    """Find the best matching section header in parsed list for a given category."""
    sections = parsed.get("sections", {})
    order = parsed.get("order", list(sections.keys()))

    if not category:
        # Default to first non-empty section or 'Items' or 'Other'
        if "Other" in sections:
            return "Other"
        if "Items" in sections:
            return "Items"
        return order[0] if order else "Items"

    clean_cat = category.strip().lower()

    # Exact match
    for sec in order:
        if sec.lower() == clean_cat:
            return sec

    # Substring / keyword match
    for sec in order:
        sec_lower = sec.lower()
        if clean_cat in sec_lower or any(word in sec_lower for word in clean_cat.split() if len(word) > 2):
            return sec

    # If not found, create new section with title case
    new_sec = category.strip().title()
    return new_sec


def normalize_input_items(items: Any, default_category: Optional[str] = None) -> list[dict[str, Any]]:
    """Normalize flexible input formats into a list of item dictionaries."""
    if items is None:
        return []

    result: list[dict[str, Any]] = []

    # Single string
    if isinstance(items, str):
        clean = items.strip()
        if clean:
            # Handle comma separated items: "milk, eggs, cheese"
            if "," in clean and "[" not in clean and "(" not in clean:
                for part in clean.split(","):
                    p = part.strip()
                    if p:
                        result.append({"name": p, "category": default_category, "quantity": None, "unit": None})
            else:
                result.append({"name": clean, "category": default_category, "quantity": None, "unit": None})
        return result

    # List of items
    if isinstance(items, list):
        for entry in items:
            if isinstance(entry, str):
                s = entry.strip()
                if s:
                    result.append({"name": s, "category": default_category, "quantity": None, "unit": None})
            elif isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("item") or entry.get("title") or "").strip()
                if name:
                    cat = entry.get("category") or default_category
                    qty = entry.get("quantity") or entry.get("count") or entry.get("qty")
                    unit = entry.get("unit") or entry.get("measurement")
                    result.append({
                        "name": name,
                        "category": cat,
                        "quantity": qty,
                        "unit": unit,
                    })

    return result


# ============================================================================
# Main High-Level List Operations
# ============================================================================


def read_list(name: str = "Groceries") -> dict[str, Any]:
    """Read a list and return its items grouped by section.

    Args:
        name: Name of the list.

    Returns:
        dict: Outcome with summary text, items list, and grouped sections.
    """
    path = ensure_list_exists(name)
    parsed = parse_list_file(path)

    sections = parsed.get("sections", {})
    total_pending = 0
    total_completed = 0
    lines: list[str] = [f"# {parsed.get('title', name)}\n"]

    for sec, items in sections.items():
        if not items:
            continue
        lines.append(f"## {sec}")
        for item in items:
            st = item.get("status", "pending")
            if st == "completed":
                total_completed += 1
                box = "[x]"
            else:
                total_pending += 1
                box = "[ ]"
            qty_part = ""
            q = item.get("quantity")
            u = item.get("unit")
            if q is not None and str(q).strip():
                if u:
                    qty_part = f" ({q} {u})"
                else:
                    qty_part = f" ({q}x)" if str(q) != "1" else ""
            elif u:
                qty_part = f" ({u})"

            lines.append(f"- {box} {item['name']}{qty_part}")
        lines.append("")

    summary = "\n".join(lines).strip()
    if total_pending == 0 and total_completed == 0:
        summary = f"The '{name}' list is currently empty."

    return {
        "status": "success",
        "name": normalize_list_name(name),
        "path": path,
        "total_pending": total_pending,
        "total_completed": total_completed,
        "summary": summary,
        "sections": sections,
    }


def add_to_list(
    name: str = "Groceries",
    items: Any = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    """Add items to a list with category routing and quantity incrementing.

    Args:
        name: Name of the list.
        items: List of item dicts or item names.
        category: Default category if not specified on item.

    Returns:
        dict: Summary of added/updated items.
    """
    path = ensure_list_exists(name)
    parsed = parse_list_file(path)
    norm_items = normalize_input_items(items, default_category=category)

    if not norm_items:
        return {"status": "error", "message": "No items provided to add."}

    sections = parsed.setdefault("sections", {})
    order = parsed.setdefault("order", [])

    added_names: list[str] = []
    incremented_names: list[str] = []

    for item_input in norm_items:
        item_name = item_input["name"]
        item_cat = item_input.get("category")
        item_qty = item_input.get("quantity")
        item_unit = item_input.get("unit")

        target_sec = find_matching_section(parsed, item_cat)
        if target_sec not in sections:
            sections[target_sec] = []
            order.append(target_sec)

        # Check if item already exists in this section (case-insensitive)
        existing_idx = -1
        for idx, ex in enumerate(sections[target_sec]):
            if ex["name"].strip().lower() == item_name.strip().lower() and ex["status"] == "pending":
                existing_idx = idx
                break

        if existing_idx >= 0:
            # Increment quantity
            ex_item = sections[target_sec][existing_idx]
            ex_qty = ex_item.get("quantity")
            if ex_qty is not None and item_qty is not None:
                try:
                    new_qty = float(ex_qty) + float(item_qty)
                    if new_qty.is_integer():
                        new_qty = int(new_qty)
                    ex_item["quantity"] = new_qty
                    if item_unit:
                        ex_item["unit"] = item_unit
                    incremented_names.append(f"{item_name} (now {new_qty} {ex_item.get('unit') or ''})".strip())
                except (ValueError, TypeError):
                    added_names.append(item_name)
            else:
                if item_qty is not None:
                    ex_item["quantity"] = item_qty
                if item_unit:
                    ex_item["unit"] = item_unit
                incremented_names.append(item_name)
        else:
            # Add new item
            sections[target_sec].append({
                "raw": format_item_line(item_name, item_qty, item_unit),
                "status": "pending",
                "name": item_name,
                "quantity": item_qty,
                "unit": item_unit,
            })
            added_names.append(item_name)

    write_list_file(path, parsed)

    msg_parts = []
    if added_names:
        msg_parts.append(f"Added {len(added_names)} item(s): {', '.join(added_names)}")
    if incremented_names:
        msg_parts.append(f"Updated quantity for: {', '.join(incremented_names)}")

    return {
        "status": "success",
        "message": " | ".join(msg_parts) or "List updated.",
        "added": added_names,
        "updated": incremented_names,
        "name": normalize_list_name(name),
    }


def toggle_list_items(
    name: str = "Groceries",
    items: Any = None,
    completed: bool = True,
) -> dict[str, Any]:
    """Mark items as completed (checked) or active (unchecked) using fuzzy matching.

    Args:
        name: Name of the list.
        items: List of item names to toggle.
        completed: True for checked `- [x]`, False for active `- [ ]`.

    Returns:
        dict: Outcome summary.
    """
    path = ensure_list_exists(name)
    parsed = parse_list_file(path)
    norm_items = normalize_input_items(items)

    if not norm_items:
        return {"status": "error", "message": "No items provided to check/uncheck."}

    target_names = [i["name"].strip().lower() for i in norm_items]
    target_status = "completed" if completed else "pending"
    matched_names: list[str] = []

    sections = parsed.get("sections", {})
    for sec, sec_items in sections.items():
        for item in sec_items:
            item_name_lower = item["name"].strip().lower()
            # Match exact or substring
            for t in target_names:
                if t == item_name_lower or t in item_name_lower or item_name_lower in t:
                    item["status"] = target_status
                    matched_names.append(item["name"])
                    break

    write_list_file(path, parsed)

    verb = "Checked off" if completed else "Unchecked"
    if matched_names:
        message = f"{verb} {len(matched_names)} item(s): {', '.join(set(matched_names))}."
    else:
        message = f"No matching items found on '{name}' list to {verb.lower()}."

    return {
        "status": "success" if matched_names else "not_found",
        "message": message,
        "matched": list(set(matched_names)),
        "name": normalize_list_name(name),
    }


def remove_from_list(
    name: str = "Groceries",
    items: Any = None,
) -> dict[str, Any]:
    """Delete specific items completely from the list.

    Args:
        name: Name of the list.
        items: List of item names to remove.

    Returns:
        dict: Outcome summary.
    """
    path = ensure_list_exists(name)
    parsed = parse_list_file(path)
    norm_items = normalize_input_items(items)

    if not norm_items:
        return {"status": "error", "message": "No items provided to remove."}

    target_names = [i["name"].strip().lower() for i in norm_items]
    removed_names: list[str] = []

    sections = parsed.get("sections", {})
    for sec in list(sections.keys()):
        keep_items = []
        for item in sections[sec]:
            item_name_lower = item["name"].strip().lower()
            matched = False
            for t in target_names:
                if t == item_name_lower or t in item_name_lower:
                    matched = True
                    removed_names.append(item["name"])
                    break
            if not matched:
                keep_items.append(item)
        sections[sec] = keep_items

    write_list_file(path, parsed)

    if removed_names:
        message = f"Removed {len(removed_names)} item(s): {', '.join(set(removed_names))}."
    else:
        message = f"No matching items found on '{name}' list to remove."

    return {
        "status": "success" if removed_names else "not_found",
        "message": message,
        "removed": list(set(removed_names)),
        "name": normalize_list_name(name),
    }


def clear_completed_items(name: str = "Groceries") -> dict[str, Any]:
    """Purge all completed `- [x]` items from the list while preserving section headings.

    Args:
        name: Name of the list.

    Returns:
        dict: Outcome summary.
    """
    path = ensure_list_exists(name)
    parsed = parse_list_file(path)

    sections = parsed.get("sections", {})
    purged_count = 0

    for sec in list(sections.keys()):
        active_items = []
        for item in sections[sec]:
            if item.get("status") == "completed":
                purged_count += 1
            else:
                active_items.append(item)
        sections[sec] = active_items

    write_list_file(path, parsed)

    return {
        "status": "success",
        "message": f"Cleared {purged_count} completed item(s) from '{name}'.",
        "cleared_count": purged_count,
        "name": normalize_list_name(name),
    }
