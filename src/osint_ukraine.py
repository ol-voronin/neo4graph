"""OSINT Ukraine sources parser and DD (Due Diligence) integration helper.

Extracts structured registries, data sources, and identifier patterns from
the OSINT_in_Ukraine knowledge base (data/OSINT_in_Ukraine/README.md) and
maps them against our DD lake, Neon database, and web evidence panel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_PATH = Path("data/OSINT_in_Ukraine/README.md")
OUT_JSON = Path("data/osint_sources.json")

# Mapping of known OSINT registries in Ukraine to our project's internal status
REGISTRY_STATUS_MAP = {
    "usr.minjust.gov.ua": {"status": "integrated", "module": "src/lake.py", "table": "edr_orgs, lake uo/fop"},
    "reyestr.court.gov.ua": {"status": "integrated", "module": "src/lake_edrsr.py", "table": "court_decisions"},
    "court.gov.ua": {"status": "integrated", "module": "docs/court_summons.md", "table": "court_summons"},
    "erb.minjust.gov.ua": {"status": "integrated", "module": "db/schema.sql", "table": "debtors"},
    "wanted.mvs.gov.ua": {"status": "integrated", "module": "src/police_wanted.py", "table": "wanted_persons"},
    "nazk.gov.ua": {"status": "integrated", "module": "local/ua_worker.py, src/nazk_corrupt.py", "table": "declarations, corrupt_persons"},
    "declarations.com.ua": {"status": "integrated", "module": "local/ua_worker.py", "table": "declarations"},
    "data.gov.ua": {"status": "integrated", "module": "src/lake.py", "table": "raw dumps lake"},
    "spending.gov.ua": {"status": "integrated", "module": "src/spending.py", "table": "public_payments"},
    "prozorro.gov.ua": {"status": "integrated", "module": "src/lake_prozorro.py", "table": "public_contracts"},
    "drrp.minjust.gov.ua": {"status": "planned", "note": "State Register of Real Property Rights"},
    "map.land.gov.ua": {"status": "external_ref", "note": "Public Cadastral Map"},
    "bankruptcy.gov.ua": {"status": "planned", "note": "Bankruptcy and insolvency proceedings"},
    "tax.gov.ua": {"status": "external_ref", "note": "State Tax Service VAT & single tax registries"},
    "me.gov.ua": {"status": "external_ref", "note": "Ministry of Economy licensing portal"},
    "dls.gov.ua": {"status": "external_ref", "note": "State Service on Medicines and Drugs"},
    "clarity-project.info": {"status": "external_ref", "note": "Clarity Project procurement analytics"},
    "youcontrol.com.ua": {"status": "external_ref", "note": "YouControl aggregator reference"},
    "dmsu.gov.ua": {"status": "external_ref", "note": "State Migration Service passport/DL check"},
    "hsc.gov.ua": {"status": "external_ref", "note": "Main Service Center of MIA vehicle/DL check"},
    "ukrstat.gov.ua": {"status": "external_ref", "note": "State Statistics Service"},
}

IDENTIFIER_PATTERNS = {
    "edrpou": {
        "description": "ЄДРПОУ юридичної особи (8 цифр)",
        "regex": r"^\d{8}$",
        "sample": "00034045",
    },
    "rnokpp": {
        "description": "РНОКПП / ІПН фізичної особи або ФОП (10 цифр)",
        "regex": r"^\d{10}$",
        "sample": "3257209987",
    },
    "passport_biometric": {
        "description": "Біометричний закордонний паспорт (2 латинські літери + 7 цифр)",
        "regex": r"^[A-Z]{2}\d{7}$",
        "sample": "AA1234567",
    },
    "passport_non_biometric": {
        "description": "Небіометричний закордонний паспорт (2 латинські літери + 6 цифр)",
        "regex": r"^[A-Z]{2}\d{6}$",
        "sample": "AA123456",
    },
    "id_card": {
        "description": "ID-картка громадянина України (9 цифр)",
        "regex": r"^\d{9}$",
        "sample": "123456789",
    },
    "driver_license_eu": {
        "description": "Посвідчення водія європейського зразка (3 латинські літери + 6 цифр)",
        "regex": r"^[A-Z]{3}\d{6}$",
        "sample": "ABC123456",
    },
    "driver_license_old": {
        "description": "Посвідчення водія старого зразка (2 кириличні літери + 8 цифр)",
        "regex": r"^[А-ЯІЇЄ]{2}\d{8}$",
        "sample": "АБ12345678",
    },
    "military_ticket": {
        "description": "Військовий квиток (10 символів, серія та номер)",
        "regex": r"^[А-ЯІЇЄA-Z]{2}\d{6,8}$",
        "sample": "АБ12345678",
    },
    "ua_mobile": {
        "description": "Мобільний номер України (+380 та 9 цифр)",
        "regex": r"^(?:\+?380|0)\d{9}$",
        "sample": "+380671234567",
    },
}

OPERATOR_PREFIXES = {
    "Kyivstar": ["67", "68", "96", "97", "98"],
    "Vodafone": ["50", "66", "95", "99"],
    "Lifecell": ["63", "73", "93"],
    "3mob": ["91"],
}


def parse_readme(readme_path: Path) -> dict:
    if not readme_path.exists():
        raise FileNotFoundError(f"Cannot find {readme_path}")

    text = readme_path.read_text(encoding="utf-8")

    # Extract all links with anchor text
    all_links = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)", text)

    # Extract Government & Semi-Official Services
    registries = []
    m_gov = re.search(
        r"## Government and Semi-Official Online Services in Ukraine\n(.*?)(?=\n## |\Z)",
        text,
        re.S,
    )
    if m_gov:
        gov_text = m_gov.group(1)
        # Parse lines like: * **[Name](URL)** – Description
        items = re.findall(
            r"\*\s*\*\*\[([^\]]+)\]\(([^\)]+)\)\*\*\s*[–\-—]\s*([^\n]+)",
            gov_text,
        )
        for name, url, desc in items:
            domain = re.sub(r"^https?://(?:www\.)?([^/]+).*", r"\1", url.strip())
            mapping = REGISTRY_STATUS_MAP.get(domain, {"status": "external_ref"})
            registries.append(
                {
                    "name": name.strip(),
                    "url": url.strip(),
                    "domain": domain,
                    "description": desc.strip(),
                    "dd_status": mapping.get("status", "external_ref"),
                    "dd_module": mapping.get("module"),
                    "dd_table": mapping.get("table"),
                    "dd_note": mapping.get("note"),
                }
            )

    # Extract Media and News
    media_list = []
    m_media = re.search(
        r"## Media and News in Ukraine\n(.*?)(?=\n## |\Z)", text, re.S
    )
    if m_media:
        items = re.findall(
            r"\[([^\]]+)\]\(([^\)]+)\)\s*[–\-—]\s*([^\n]+)", m_media.group(1)
        )
        for name, url, desc in items:
            media_list.append(
                {
                    "name": name.strip(),
                    "url": url.strip(),
                    "description": desc.strip(),
                }
            )

    # Extract Marketplaces and Local Platforms
    platforms_list = []
    m_plat = re.search(
        r"## Major Local Data Platforms in Ukraine\n(.*?)(?=\n## |\Z)", text, re.S
    )
    if m_plat:
        items = re.findall(
            r"\[([^\]]+)\]\(([^\)]+)\)\s*[–\-—]\s*([^\n]+)", m_plat.group(1)
        )
        for name, url, desc in items:
            platforms_list.append(
                {
                    "name": name.strip(),
                    "url": url.strip(),
                    "description": desc.strip(),
                }
            )

    return {
        "source": "https://github.com/OSINT-for-countries/OSINT_in_Ukraine",
        "description": "Structured OSINT resources & registers catalog for Ukraine Due Diligence",
        "total_extracted_links": len(all_links),
        "identifiers": IDENTIFIER_PATTERNS,
        "mobile_operators": OPERATOR_PREFIXES,
        "registries": registries,
        "media": media_list,
        "platforms": platforms_list,
    }


def main():
    data = parse_readme(REPO_PATH)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Successfully generated {OUT_JSON}")
    print(f"Extracted {len(data['registries'])} government/official registers")
    print(f"Extracted {len(data['media'])} media outlets")
    print(f"Extracted {len(data['platforms'])} local platforms")
    print(f"Configured {len(data['identifiers'])} identifier validators")


if __name__ == "__main__":
    main()
