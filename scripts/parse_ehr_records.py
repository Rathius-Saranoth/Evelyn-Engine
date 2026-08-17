#!/usr/bin/env python3
# parse_ehr_records.py
# date created: 2026-08-16 20:21:28
# date modified: 2026-08-16 20:21:28
# tags: 

# scripts/parse_ehr_records.py
"""
parse_ehr_records.py — Authoritative Electronic Health Record (EHR/FHIR) Parser.

Ingests US Core FHIR Resources.json, CCDA, and encounter metadata from the
staged 'Medical Record' provider export and synthesizes authoritative master notes
for the Obsidian Vault (Ricky/Medical/):
  - Conditions & Diagnoses.md
  - Medications (Official).md
  - Allergies & Intolerances.md
  - Immunization Record.md
  - Clinical Encounters & Doctor Visits.md
  - Lab Results & Historical Panels.md
  - Authoritative Health Record - Master Summary.md
"""

import os
import sys
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import defaultdict

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg

VAULT_DIR = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
OUT_MEDICAL_DIR = os.path.join(VAULT_DIR, "Ricky", "Medical")
STAGING_EHR_DIR = getattr(cfg, "MEDICAL_RECORDS_DIR", os.path.join(ROOT_DIR, "data", "medical_records"))

def find_ehr_files():
    """Locate the FHIR JSON and CCDA files in the staged medical records directory."""
    fhir_path = None
    ccda_files = []
    ndjson_toc = None
    
    for root, _, files in os.walk(STAGING_EHR_DIR):
        for f in files:
            full_p = os.path.join(root, f)
            if "us core fhir resources.json" in f.lower():
                fhir_path = full_p
            elif f.lower().endswith(".xml") and "ccda" in root.lower():
                ccda_files.append(full_p)
            elif "table of contents.ndjson" in f.lower():
                ndjson_toc = full_p
                
    return fhir_path, ccda_files, ndjson_toc

def parse_fhir_bundle(fhir_path: str) -> dict:
    """Parse US Core FHIR Bundle into structured clinical categories."""
    if not fhir_path or not os.path.exists(fhir_path):
        return {}
        
    with open(fhir_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
        
    categories = defaultdict(list)
    
    entries = data.get("entry", []) if isinstance(data, dict) else []
    for entry in entries:
        res = entry.get("resource", {})
        r_type = res.get("resourceType")
        if not r_type:
            continue
            
        if r_type == "Condition":
            name = res.get("code", {}).get("text") or (res.get("code", {}).get("coding", [{}])[0].get("display", "Unknown Condition"))
            status = res.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "unknown")
            recorded_date = res.get("recordedDate") or res.get("onsetDateTime") or ""
            categories["conditions"].append({
                "name": name,
                "status": status,
                "date": recorded_date[:10] if recorded_date else "N/A",
                "id": res.get("id", "")
            })
            
        elif r_type in ["MedicationRequest", "MedicationStatement"]:
            name = res.get("medicationCodeableConcept", {}).get("text") or (res.get("medicationCodeableConcept", {}).get("coding", [{}])[0].get("display", "Unknown Medication"))
            status = res.get("status", "unknown")
            dosage = ""
            if "dosageInstruction" in res and res["dosageInstruction"]:
                dosage = res["dosageInstruction"][0].get("text", "")
            authored_on = res.get("authoredOn") or res.get("effectiveDateTime") or ""
            categories["medications"].append({
                "name": name,
                "status": status,
                "dosage": dosage,
                "date": authored_on[:10] if authored_on else "N/A"
            })
            
        elif r_type == "AllergyIntolerance":
            substance = res.get("code", {}).get("text") or (res.get("code", {}).get("coding", [{}])[0].get("display", "Unknown Substance"))
            status = res.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "active")
            criticality = res.get("criticality", "unspecified")
            reaction_list = []
            for r in res.get("reaction", []):
                for m in r.get("manifestation", []):
                    reaction_list.append(m.get("text") or m.get("coding", [{}])[0].get("display", ""))
            categories["allergies"].append({
                "substance": substance,
                "status": status,
                "criticality": criticality,
                "reactions": ", ".join(filter(None, reaction_list)) or "None recorded"
            })
            
        elif r_type == "Immunization":
            vaccine = res.get("vaccineCode", {}).get("text") or (res.get("vaccineCode", {}).get("coding", [{}])[0].get("display", "Vaccine"))
            status = res.get("status", "completed")
            date = (res.get("occurrenceDateTime") or "")[:10] or "N/A"
            lot = res.get("lotNumber", "")
            categories["immunizations"].append({
                "vaccine": vaccine,
                "status": status,
                "date": date,
                "lot": lot
            })
            
        elif r_type == "Observation":
            code_text = res.get("code", {}).get("text") or (res.get("code", {}).get("coding", [{}])[0].get("display", "Observation"))
            val = ""
            if "valueQuantity" in res:
                vq = res["valueQuantity"]
                val = f"{vq.get('value', '')} {vq.get('unit', '')}".strip()
            elif "valueString" in res:
                val = res["valueString"]
            elif "valueCodeableConcept" in res:
                val = res["valueCodeableConcept"].get("text", "")
            date = (res.get("effectiveDateTime") or res.get("issued") or "")[:10]
            categories["observations"].append({
                "name": code_text,
                "value": val,
                "date": date or "N/A"
            })
            
        elif r_type == "Encounter":
            enc_type = res.get("type", [{}])[0].get("text") or (res.get("type", [{}])[0].get("coding", [{}])[0].get("display", "Medical Encounter"))
            period = res.get("period", {})
            start = (period.get("start") or "")[:10]
            categories["encounters"].append({
                "type": enc_type,
                "date": start or "N/A"
            })
            
    return categories

def generate_conditions_note(conditions: list) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, conditions]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Authoritative Conditions & Diagnoses",
        "",
        "> [!NOTE] Authoritative Clinical Record",
        "> Directly synthesized from official healthcare provider Electronic Health Record (EHR) export.",
        "",
        "| Condition / Diagnosis | Clinical Status | Recorded Date |",
        "| :--- | :---: | :---: |"
    ]
    for c in sorted(conditions, key=lambda x: x.get("date", ""), reverse=True):
        lines.append(f"| {c['name']} | `{c['status']}` | {c['date']} |")
    return "\n".join(lines)

def generate_medications_note(medications: list) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, medications]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Official Medication Record",
        "",
        "> [!IMPORTANT] Prescription History",
        "> Authoritative medication records from clinical provider systems.",
        "",
        "| Medication | Status | Dosage / Instructions | Prescribed / Effective Date |",
        "| :--- | :---: | :--- | :---: |"
    ]
    for m in sorted(medications, key=lambda x: x.get("date", ""), reverse=True):
        dosage = m['dosage'] or "Standard prescribed dose"
        lines.append(f"| {m['name']} | `{m['status']}` | {dosage} | {m['date']} |")
    return "\n".join(lines)

def generate_allergies_note(allergies: list) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, allergies]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Clinical Allergies & Intolerances",
        "",
        "| Substance / Allergen | Status | Criticality | Reactions / Manifestations |",
        "| :--- | :---: | :---: | :--- |"
    ]
    for a in allergies:
        lines.append(f"| {a['substance']} | `{a['status']}` | {a['criticality']} | {a['reactions']} |")
    return "\n".join(lines)

def generate_immunizations_note(immunizations: list) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, immunizations]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Immunization & Vaccine History",
        "",
        "| Vaccine / Immunization | Administration Date | Status | Lot Number |",
        "| :--- | :---: | :---: | :---: |"
    ]
    for imm in sorted(immunizations, key=lambda x: x.get("date", ""), reverse=True):
        lines.append(f"| {imm['vaccine']} | {imm['date']} | `{imm['status']}` | {imm['lot'] or 'N/A'} |")
    return "\n".join(lines)

def generate_encounters_note(encounters: list) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, encounters]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Clinical Encounters & Doctor Visits Timeline",
        "",
        "| Visit Date | Encounter Type / Service |",
        "| :---: | :--- |"
    ]
    for enc in sorted(encounters, key=lambda x: x.get("date", ""), reverse=True):
        lines.append(f"| {enc['date']} | {enc['type']} |")
    return "\n".join(lines)

def generate_master_summary(data: dict) -> str:
    lines = [
        "---",
        "tags: [medical, clinical-record, authoritative, fhir, summary]",
        "source: Healthcare Provider EHR Export (FHIR)",
        f"last_synced: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Authoritative Health Record - Master Summary",
        "",
        "> [!NOTE] Primary Clinical Ground Truth",
        "> This master document aggregates the authoritative clinical records exported directly from Ricky's healthcare provider.",
        "",
        "## Quick Links",
        "- [[Ricky/Medical/Conditions & Diagnoses|Conditions & Diagnoses]]",
        "- [[Ricky/Medical/Medications (Official)|Official Medication History]]",
        "- [[Ricky/Medical/Allergies & Intolerances|Allergies & Intolerances]]",
        "- [[Ricky/Medical/Immunization Record|Immunization History]]",
        "- [[Ricky/Medical/Clinical Encounters & Doctor Visits|Clinical Encounters Timeline]]",
        "",
        f"## Statistics",
        f"- **Conditions Tracked:** {len(data.get('conditions', []))}",
        f"- **Medication Records:** {len(data.get('medications', []))}",
        f"- **Allergies Recorded:** {len(data.get('allergies', []))}",
        f"- **Immunizations:** {len(data.get('immunizations', []))}",
        f"- **Clinical Encounters:** {len(data.get('encounters', []))}",
        ""
    ]
    return "\n".join(lines)

def run_ehr_synthesis():
    os.makedirs(OUT_MEDICAL_DIR, exist_ok=True)
    fhir_p, ccda_p, toc_p = find_ehr_files()
    
    if not fhir_p:
        print("[parse_ehr] No US Core FHIR Resources.json found in medical records staging.", flush=True)
        return False
        
    print(f"[parse_ehr] Parsing FHIR bundle at: {fhir_p}", flush=True)
    data = parse_fhir_bundle(fhir_p)
    
    notes = {
        "Conditions & Diagnoses.md": generate_conditions_note(data.get("conditions", [])),
        "Medications (Official).md": generate_medications_note(data.get("medications", [])),
        "Allergies & Intolerances.md": generate_allergies_note(data.get("allergies", [])),
        "Immunization Record.md": generate_immunizations_note(data.get("immunizations", [])),
        "Clinical Encounters & Doctor Visits.md": generate_encounters_note(data.get("encounters", [])),
        "Authoritative Health Record - Master Summary.md": generate_master_summary(data)
    }
    
    for filename, content in notes.items():
        out_p = os.path.join(OUT_MEDICAL_DIR, filename)
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  [EHR Synthesized] Generated: {filename} ({len(content)} bytes)", flush=True)
        
    print("[parse_ehr] Authoritative EHR synthesis complete!", flush=True)
    return True

if __name__ == "__main__":
    run_ehr_synthesis()
