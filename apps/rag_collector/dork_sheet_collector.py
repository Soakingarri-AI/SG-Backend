#!/usr/bin/env python3
"""
dork_sheet_collector.py
=======================

Long-running discovery service that hunts for open-access academic / historical /
educational PDFs (African history, civilisations, STEM & education), validates
each hit, and appends the cleaned metadata straight into a Google Sheet.

Designed to be parked on an Ubuntu EC2 box under systemd and left running for
days at a time.

Two discovery channels feed one pipeline
----------------------------------------
    QueryPlanner  ->  SearchScraper  --.
    (what to ask)     (DuckDuckGo)      \
                                         >-  URLProcessor -> SheetsManager
    HarvestPlanner -> repository APIs   /    (verify+clean)   (batched append)
    (whose catalogue)  (OpenAlex, ...) -'

Dorking found ~26 documents per 1,785 queries once the obvious ground was
covered - a web search caps its result set however the query is reworded.
`source_harvesters.py` therefore asks repositories for their holdings directly,
paginating through them, and brings the authors along with the metadata.

Concurrency note
----------------
Search engines are queried strictly serially -- that is the whole point of the
rate limiting -- but URL verification is network-bound and embarrassingly
parallel, so it runs across a small ThreadPoolExecutor. Threads are used in
preference to asyncio so the dependency set stays on `requests`, which is both
what was specified and what has the battle-tested adapter/retry story.

Sheet columns
-------------
    A  Subject / Concept
    B  Source
    C  Hyperlink        <- deduplication key
    D  Title of source
    E  Open source      (TRUE / FALSE)
    F  Author           "A; B; C" - see author_resolver.py; blank = unknown

Usage
-----
    python dork_sheet_collector.py                     # run forever
    python dork_sheet_collector.py --once              # single pass, then exit
    python dork_sheet_collector.py --dry-run           # never touch the sheet
    python dork_sheet_collector.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

import gspread
import requests
from google.oauth2.service_account import Credentials
from pydantic import BaseModel, Field, field_validator

from author_resolver import (
    AuthorResolver,
    clean_name,
    format_authors,
    is_plausible_creator,
)
from source_harvesters import (
    HarvestPlanner,
    HarvestRecord,
    concept_matcher,
    phrase_in_text,
)

# The upstream project renamed `duckduckgo_search` to `ddgs`. Support both, but
# strongly prefer `ddgs`: the sunset `duckduckgo_search` wheel returns zero
# results for every query on current infrastructure. The two differ in the name
# of the query argument and in what `backend` means, so track which one we got.
try:  # pragma: no cover - environment dependent
    from ddgs import DDGS  # maintained package

    _DDGS_MODERN = True
    _DDGS_QUERY_KWARG = "query"
except ImportError:  # pragma: no cover - environment dependent
    from duckduckgo_search import DDGS  # legacy, effectively dead

    _DDGS_MODERN = False
    _DDGS_QUERY_KWARG = "keywords"


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent

SHEET_ID_DEFAULT = "1SkQTmXcmoLAAzx2ePvdaiprbN9X_CQoNkFTRU3VFIOQ"
WORKSHEET_DEFAULT = "Sheet1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

HEADER_ROW = [
    "Subject / Concept",
    "Source",
    "Hyperlink",
    "Title of source",
    "Open source",
    "Author",
]

# Politeness / anti-blocking knobs. Tune these before you tune anything else.
SEARCH_SLEEP_RANGE = (5.0, 12.0)      # seconds between individual searches
CATEGORY_SLEEP_RANGE = (60.0, 120.0)  # seconds between categories
CYCLE_SLEEP_RANGE = (300.0, 600.0)    # seconds between full passes (5-10 min)
SEARCH_MAX_RETRIES = 4                # attempts per query before giving up
SEARCH_BACKOFF_BASE = 20.0            # seconds; doubles per retry, plus jitter
# Ceiling on a single inter-attempt pause. Kept deliberately low: the corpus is
# ~6k queries against a fixed time budget, so abandoning one unlucky query is
# far cheaper than stalling four minutes on it. Sustained throttling is handled
# by cool_down_if_struggling(), not by ever-growing per-attempt waits.
SEARCH_BACKOFF_MAX = 90.0

# Search backends, in preference order, rotated per attempt.
#
# This ordering is empirical and *environment dependent* - measure before you
# reorder. Both `brave` and `bing` honour `filetype:`/`site:` operators, but
# reachability differs sharply by source IP. Measured over 182 attempts from an
# EC2 (datacenter) IP:
#
#     bing        58 ok / 17 fail   77%
#     auto        20 ok / 17 fail   54%
#     brave        2 ok / 55 fail    3%   <- blocks the AWS range
#     duckduckgo   0 ok / 36 fail    0%   <- dropped; never once succeeded
#
# From a residential IP brave scored 8/8 on the same dorks, so do not assume
# this order transfers - re-measure if you move hosts. `bing` leads because a
# wasted first attempt costs ~15-20s on *every* query. `duckduckgo` is absent
# rather than last: 36 attempts, zero successes, so reaching it only burns
# clock. With three backends and four attempts the rotation still tries every
# one of them before giving up.
SEARCH_BACKENDS = ("bing", "auto", "brave")

#: Harvest one page from one repository every N search queries. Harvesting is
#: cheap and high-yield, so this is deliberately frequent; --harvest-only skips
#: searching altogether and just works through the repositories.
HARVEST_EVERY_QUERIES = 3

RESULTS_PER_QUERY = 25
VERIFY_WORKERS = 6
VERIFY_TIMEOUT = (10, 25)             # (connect, read) seconds
SHEET_BATCH_SIZE = 8                  # rows buffered before a sheet write
SHEET_MAX_ROW_AGE = 600.0             # ...or flushed once the oldest has waited this long (s)
SHEET_MAX_RETRIES = 6

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# Domains that are open access regardless of TLD.
OPEN_ACCESS_DOMAINS = {
    "unesco.org", "unesdoc.unesco.org", "un.org", "archive.org", "openstax.org",
    "arxiv.org", "zenodo.org", "core.ac.uk", "doaj.org", "hal.science",
    "openedition.org", "revues.org", "gutenberg.org", "biodiversitylibrary.org",
    "worldbank.org", "openknowledge.worldbank.org", "researchgate.net",
    "semanticscholar.org", "citeseerx.ist.psu.edu", "ajol.info", "scielo.org",
    "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "eric.ed.gov",
    "africaportal.org", "opendocs.ids.ac.uk", "oapen.org", "jstor.org.proxy",
    # Hosts reached through the repository harvesters (source_harvesters.py).
    "hal.science", "archives-ouvertes.fr", "doabooks.org", "persee.fr",
    "openedition.org", "uct.ac.za", "sun.ac.za", "uwc.ac.za", "ru.ac.za",
    "ug.edu.gh", "mak.ac.ug", "unisa.ac.za", "scielo.org.za", "scielo.br",
}

# Paywalled publishers and content mills. Checked *before* the TLD heuristic,
# because plenty of them sit on .org (jstor.org, learned societies, ...).
CLOSED_ACCESS_DOMAINS = {
    "jstor.org", "sciencedirect.com", "springer.com", "link.springer.com",
    "tandfonline.com", "wiley.com", "onlinelibrary.wiley.com", "cambridge.org",
    "oup.com", "academic.oup.com", "sagepub.com", "journals.sagepub.com",
    "elsevier.com", "degruyter.com", "brill.com", "emerald.com", "ieee.org",
    "ieeexplore.ieee.org", "acm.org", "dl.acm.org", "scribd.com",
    "coursehero.com", "studocu.com", "academia.edu", "chegg.com",
}

# Open-ish TLD suffixes, used only when neither explicit list matched.
OPEN_TLD_SUFFIXES = (
    ".edu", ".ac.uk", ".edu.ng", ".ac.za", ".edu.gh", ".ac.ke", ".edu.au",
    ".ac.in", ".gov", ".gov.ng", ".int", ".org",
)

# URL shapes that identify a *document item* inside a repository, as opposed to
# a preface, a browse page or a login form.
#
# This exists because "any HTML page on an open-access host" is far too loose a
# rule: it happily admits openstax.org book prefaces and help.openstax.org login
# pages, none of which are documents. A non-PDF is only ever accepted when its
# URL matches one of these.
REPOSITORY_ITEM_PATTERNS = (
    re.compile(r"/details/[^/]+"),          # archive.org
    re.compile(r"/ark:/"),                  # unesdoc.unesco.org
    re.compile(r"/handle/\d+"),             # DSpace (worldbank, IDS, many unis)
    re.compile(r"/records?/\d+"),           # Zenodo / Invenio
    re.compile(r"/download/pdf"),           # CORE
    re.compile(r"/abs/\d", re.IGNORECASE),  # arXiv
    re.compile(r"[?&]id=[A-Z]{2}\d+"),      # ERIC
    re.compile(r"/publication/\d+"),        # ResearchGate
    re.compile(r"/stream/[^/]+"),           # archive.org stream view
)

# Trailing boilerplate repositories bolt onto their page titles. Matched
# case-insensitively against the last " : " / " - " / " | " segment of a title,
# and stripped repeatedly - archive.org alone contributes two of these.
TITLE_BOILERPLATE = (
    "internet archive",
    "free download, borrow, and streaming",
    "free download borrow and streaming",
    "unesco digital library",
    "unesdoc digital library",
    "google books",
    "researchgate",
    "semantic scholar",
    "core",
    "pdf",
    "full text",
    "archive.org",
)

# Hosts we never want to store even if the search engine returns them.
BLOCKED_HOST_FRAGMENTS = (
    "facebook.", "twitter.", "x.com", "instagram.", "pinterest.", "tiktok.",
    "youtube.", "linkedin.", "reddit.", "quora.", "amazon.", "ebay.",
    "duckduckgo.com", "bing.com", "sci-hub", "libgen", "z-lib",
)


@dataclass(frozen=True)
class Category:
    """A themed bundle of concepts crossed with dork templates."""

    name: str
    concepts: tuple[str, ...]
    dorks: tuple[str, ...]

    def queries(self) -> list[tuple[str, str]]:
        """Return every (concept, query-string) pair for this category."""
        return [
            (concept, f'"{concept}" {dork}')
            for concept in self.concepts
            for dork in self.dorks
        ]


# --------------------------------------------------------------------------- #
#  Dork corpus
# --------------------------------------------------------------------------- #
#
# Sizing note: a cycle runs every (concept x dork) pair once, with a randomly
# chosen modifier. At the ~22s/query measured on a t3.micro, a 3-day run gets
# through roughly 11,800 queries. The corpus below is deliberately larger than
# that budget so a long run keeps finding new ground instead of re-asking
# questions it has already answered.

#: Dorks that make sense for literally any topic here.
CORE_DORKS: tuple[str, ...] = (
    "filetype:pdf site:.edu",
    "filetype:pdf site:.org",
    "filetype:pdf site:archive.org",
    "filetype:pdf site:core.ac.uk",
    "filetype:pdf site:ajol.info",
    "filetype:pdf site:jstor.org",
    "filetype:pdf site:researchgate.net",
    "filetype:pdf site:ac.za",
    "filetype:pdf site:ac.uk",
    "filetype:pdf site:edu.ng",
    "filetype:pdf site:zenodo.org",
    "filetype:pdf site:oapen.org",
    'filetype:pdf "journal article"',
    'filetype:pdf "working paper"',
    "filetype:pdf thesis",
    "filetype:pdf dissertation",
)

#: History/humanities flavoured additions.
HISTORY_DORKS: tuple[str, ...] = CORE_DORKS + (
    "filetype:pdf site:unesco.org",
    'filetype:pdf "archaeology"',
    'filetype:pdf "historiography"',
    'filetype:pdf "oral tradition"',
)

#: Science flavoured additions.
STEM_DORKS: tuple[str, ...] = CORE_DORKS + (
    "filetype:pdf site:openstax.org",
    'filetype:pdf "indigenous knowledge"',
    'filetype:pdf "ethnoscience"',
    'filetype:pdf "field study"',
)

#: Schooling / examination flavoured additions.
EDUCATION_DORKS: tuple[str, ...] = CORE_DORKS + (
    "filetype:pdf site:openstax.org",
    "filetype:pdf site:eric.ed.gov",
    'filetype:pdf "past questions"',
    'filetype:pdf "syllabus"',
    'filetype:pdf "curriculum"',
)


CATEGORIES: tuple[Category, ...] = (
    Category(
        name="African Empires, Kingdoms & States",
        concepts=(
            "Nok culture",
            "Mali Empire",
            "Songhai Empire",
            "Ghana Empire",
            "Oyo Empire",
            "Benin Kingdom",
            "Kanem-Bornu",
            "Bornu Empire",
            "Great Zimbabwe",
            "Kingdom of Kush",
            "Ancient Nubia",
            "Kingdom of Aksum",
            "Zagwe dynasty",
            "Solomonic dynasty Ethiopia",
            "Kingdom of Kongo",
            "Kingdom of Ndongo",
            "Luba Empire",
            "Lunda Empire",
            "Kingdom of Buganda",
            "Kingdom of Bunyoro",
            "Kingdom of Rwanda",
            "Swahili city-states",
            "Kilwa Kisiwani",
            "Sultanate of Zanzibar",
            "Mutapa Empire",
            "Rozvi Empire",
            "Zulu Kingdom",
            "Ndebele Kingdom",
            "Basotho Kingdom",
            "Asante Empire",
            "Kingdom of Dahomey",
            "Fante Confederacy",
            "Denkyira",
            "Akwamu",
            "Ife kingdom",
            "Nri Kingdom",
            "Aro Confederacy",
            "Sokoto Caliphate",
            "Fulani Jihad",
            "Hausa Kingdoms",
            "Kano Chronicle",
            "Jolof Empire",
            "Futa Toro",
            "Futa Jallon",
            "Toucouleur Empire",
            "Wassoulou Empire",
            "Samori Ture",
            "Massina Empire",
            "Bamana Empire",
            "Kaabu Kingdom",
            "Funj Sultanate",
            "Darfur Sultanate",
            "Wadai Empire",
            "Baguirmi Kingdom",
            "Almoravid dynasty",
            "Almohad Caliphate",
            "Fatimid Caliphate",
            "Mamluk Sultanate Egypt",
            "Ancient Carthage",
            "Kingdom of Numidia",
            "Garamantes",
            "Kingdom of Makuria",
            "Kingdom of Alodia",
            "Kingdom of Nobatia",
            "Adal Sultanate",
            "Ifat Sultanate",
            "Merina Kingdom Madagascar",
            "Mapungubwe",
            "Kingdom of Sennar",
            "Wolof states Senegal",
            "Bornu Kanuri state",
            "Yoruba city states",
            "Igala Kingdom",
            "Jukun Kwararafa",
            "Nupe Kingdom",
            "Borgu Kingdom",
            "Kingdom of Loango",
            "Ovimbundu kingdoms",
            "Shona states",
            "Tswana chiefdoms",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "Kingdom of Sine",
            "Kingdom of Saloum",
            "Cayor kingdom",
            "Takrur",
            "Bonoman",
            "Kong Empire",
            "Gyaaman",
            "Allada Kingdom",
            "Kingdom of Whydah",
            "Ketu Kingdom",
            "Ijebu Kingdom",
            "Ibadan Empire",
            "Kingdom of Warri",
            "Efik Calabar states",
            "Bonny Kingdom",
            "Opobo Kingdom",
            "Sayfawa dynasty",
            "Shilluk Kingdom",
            "Azande Kingdom",
            "Mangbetu Kingdom",
            "Kuba Kingdom",
            "Kazembe Kingdom",
            "Barotseland Lozi Kingdom",
            "Gaza Empire",
            "Maravi Empire",
            "Bemba Kingdom",
            "Ankole Kingdom",
            "Toro Kingdom",
            "Ajuran Sultanate",
            "Geledi Sultanate",
            "Majeerteen Sultanate",
            "Harar Emirate",
            "Kingdom of Kaffa",
            "Gadaa system Oromo",
            "Gondar period Ethiopia",
            "Mahdist State Sudan",
            "Sakalava Kingdom",
            "Tunjur Sultanate",
            "Khami kingdom",
            "Bunyoro-Kitara empire",
        ),
        dorks=HISTORY_DORKS,
    ),
    Category(
        name="African Archaeology & Material Culture",
        concepts=(
            "Nok terracotta",
            "Ife bronze heads",
            "Benin Bronzes",
            "Igbo-Ukwu bronzes",
            "African iron smelting",
            "African metallurgy history",
            "Bantu expansion archaeology",
            "Saharan rock art",
            "Tassili n'Ajjer rock art",
            "Laas Geel rock art",
            "Tsodilo Hills",
            "Blombos Cave",
            "Border Cave",
            "Sibudu Cave",
            "Olduvai Gorge",
            "Laetoli footprints",
            "Great Zimbabwe architecture",
            "Swahili stone towns",
            "Aksum stelae",
            "Meroe pyramids",
            "Nubian pyramids",
            "Kerma culture",
            "Napata",
            "Gebel Barkal",
            "Djenne-Djenno",
            "Tichitt tradition",
            "Koumbi Saleh",
            "Timbuktu manuscripts",
            "Sankore Madrasah",
            "Gao Saney",
            "Walata",
            "Thulamela",
            "Bokoni terraces",
            "African pottery traditions",
            "African beadwork archaeology",
            "West African archaeology",
            "East African archaeology",
            "Central African archaeology",
            "Southern African Iron Age",
            "African urbanism precolonial",
            "African earthen architecture",
            "Great Mosque of Djenne",
            "Lalibela rock-hewn churches",
            "Sungbo Eredo earthworks",
            "Benin City walls",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "Kintampo complex",
            "Iwo Eleru",
            "Shum Laka",
            "Dufuna canoe",
            "Daima mound",
            "Sao civilisation",
            "Bura culture",
            "Koma Land figurines",
            "Akan goldweights",
            "Engaruka",
            "Songo Mnara",
            "Gedi ruins",
            "Shanga Lamu",
            "Chibuene",
            "Toutswe",
            "Khami ruins",
            "Danangombe",
            "Naletale",
            "Bigo bya Mugenyi",
            "Ntusi",
            "Kerma deffufa",
            "Sai Island",
            "Amara West",
            "Qasr Ibrim",
            "Faras frescoes",
            "Old Dongola",
            "Soba East",
            "Adulis",
            "Yeha temple",
            "Debre Damo",
            "Harlaa",
            "Tadmekka",
            "Senegambian stone circles",
            "Wassu stone circles",
            "Ile-Ife glass beads",
        ),
        dorks=HISTORY_DORKS,
    ),
    Category(
        name="African Trade, Economy & Society",
        concepts=(
            "Trans-Saharan trade routes",
            "Sahara salt trade",
            "Wangara gold trade",
            "Indian Ocean trade Swahili coast",
            "Atlantic slave trade",
            "trans-Saharan slave trade",
            "abolition of slavery Africa",
            "African caravan routes",
            "cowrie shell currency Africa",
            "manilla currency West Africa",
            "kola nut trade",
            "African textile trade history",
            "African salt production history",
            "gold mining precolonial Africa",
            "African market systems precolonial",
            "African guilds and craft specialisation",
            "land tenure precolonial Africa",
            "African pastoralism history",
            "African agricultural history",
            "yam cultivation West Africa history",
            "sorghum millet domestication Africa",
            "African fishing economies history",
            "colonial economy Africa",
            "cash crop economy colonial Africa",
            "African railways colonial history",
            "indirect rule Nigeria",
            "Berlin Conference 1884",
            "scramble for Africa",
            "African resistance to colonial rule",
            "African nationalism independence",
            "Pan-Africanism history",
            "African diaspora history",
            "African urbanisation history",
            "African migration history",
            "African kinship systems",
            "African age-grade systems",
            "African women in precolonial economy",
            "African slavery indigenous systems",
            "African legal systems customary law",
            "African religion precolonial",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "palm oil trade Niger Delta",
            "groundnut trade Senegambia",
            "rubber trade Congo Free State",
            "ivory trade East Africa",
            "gum arabic trade",
            "dhow trade Indian Ocean",
            "Red Sea trade Africa",
            "Tuareg caravan trade",
            "hajj routes West Africa",
            "Witwatersrand gold mining history",
            "Copperbelt mining history",
            "migrant labour Southern Africa",
            "forced labour colonial Africa",
            "colonial taxation Africa",
            "currency boards colonial Africa",
            "African labour movements history",
            "African cooperative societies",
            "informal economy Africa history",
            "famine history Africa",
            "rinderpest panzootic Africa",
            "epidemic disease history Africa",
            "structural adjustment Africa",
            "African food systems history",
            "Sahel drought history",
            "African chieftaincy institutions",
        ),
        dorks=HISTORY_DORKS,
    ),
    Category(
        name="African Mathematics, Astronomy & Indigenous Science",
        concepts=(
            "African indigenous mathematics",
            "Ethnomathematics Africa",
            "Ishango bone",
            "Lebombo bone",
            "African fractals",
            "sona sand drawings",
            "Yoruba numeral system",
            "Igbo numeral system",
            "African counting systems",
            "African geometry indigenous",
            "African astronomy history",
            "Dogon astronomy",
            "Nabta Playa",
            "Borana calendar",
            "African calendar systems",
            "Egyptian mathematics Rhind papyrus",
            "Moscow mathematical papyrus",
            "African traditional medicine",
            "African ethnobotany",
            "African pharmacopoeia",
            "African indigenous knowledge systems",
            "African traditional engineering",
            "bloomery iron smelting Africa",
            "African textile dyeing chemistry",
            "African food fermentation science",
            "African irrigation systems indigenous",
            "Marakwet irrigation",
            "Konso terracing",
            "African soil management indigenous",
            "African veterinary knowledge indigenous",
            "African navigation and wayfinding",
            "African boat building technology",
            "African architecture engineering indigenous",
            "African games mathematics mancala",
            "African divination systems mathematics",
            "Ifa divination binary",
            "African music mathematics rhythm",
            "African weaving patterns mathematics",
            "African climate knowledge indigenous",
            "African biodiversity traditional knowledge",
            "science and technology in precolonial Africa",
            "African contributions to science history",
            "African mathematics education research",
            "decolonising mathematics curriculum Africa",
            "culturally responsive mathematics teaching Africa",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "Haya iron steel technology",
            "Namoratunga megaliths",
            "Timbuktu astronomy manuscripts",
            "African indigenous astronomy",
            "zai pits farming",
            "stone bunds Sahel",
            "agroforestry parkland Sahel",
            "indigenous water harvesting Africa",
            "traditional beekeeping Africa",
            "indigenous fisheries management Africa",
            "shea butter processing",
            "African indigenous fermentation",
            "ethnoveterinary medicine Africa",
            "African medicinal plants research",
            "lost-wax casting Africa",
            "African glass production archaeology",
            "African salt evaporation technology",
            "indigenous soil classification Africa",
            "African ethnoclimatology",
            "African indigenous forecasting seasons",
            "mancala game theory",
            "African string figures mathematics",
            "Egyptian astronomy Dendera",
            "African indigenous measurement systems",
            "African granary technology",
        ),
        dorks=STEM_DORKS,
    ),
    Category(
        name="African Education, Curricula & Examinations",
        concepts=(
            "WAEC syllabus",
            "WASSCE past questions",
            "NECO past questions",
            "JAMB UTME syllabus",
            "JAMB physics",
            "JAMB chemistry",
            "JAMB biology",
            "JAMB mathematics",
            "JAMB literature in English",
            "Common Entrance examination Nigeria",
            "Nigerian national curriculum basic education",
            "Ghana Education Service syllabus",
            "BECE past questions Ghana",
            "KCSE past papers",
            "KCPE past papers",
            "South Africa matric past papers",
            "CAPS curriculum South Africa",
            "Cambridge IGCSE Africa",
            "curriculum development Africa",
            "teacher education Africa",
            "STEM education Africa",
            "science education in Africa curriculum",
            "mathematics education Africa",
            "girls education Africa",
            "mother tongue instruction Africa",
            "language of instruction Africa",
            "African higher education history",
            "University of Ibadan history",
            "Makerere University history",
            "Fourah Bay College history",
            "missionary education Africa history",
            "colonial education policy Africa",
            "Islamic education Africa history",
            "Quranic schools West Africa",
            "African indigenous education systems",
            "apprenticeship training Africa traditional",
            "technical vocational education Africa",
            "open educational resources Africa",
            "e-learning Africa",
            "education policy Nigeria",
            "education policy Kenya",
            "education policy Ghana",
            "primary education access Africa",
            "secondary school textbooks Africa",
            "examination malpractice Africa research",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "NABTEB past questions",
            "UBEC basic education Nigeria",
            "NECTA past papers Tanzania",
            "UNEB past papers Uganda",
            "ECZ past papers Zambia",
            "ZIMSEC past papers",
            "BEC examinations Botswana",
            "NSSC Namibia curriculum",
            "REB curriculum Rwanda",
            "Ethiopia national curriculum",
            "African Virtual University",
            "distance education Africa",
            "TVET policy Africa",
            "decolonising curriculum South Africa",
            "inclusive education Africa",
            "special needs education Africa",
            "early childhood education Africa",
            "school feeding programmes Africa",
            "teacher professional development Africa",
            "literacy assessment Africa",
            "SACMEQ assessment",
            "PASEC assessment",
            "African universities research output",
            "school textbooks development Africa",
            "education financing Africa",
        ),
        dorks=EDUCATION_DORKS,
    ),
    Category(
        name="African Languages, Literature & Arts",
        concepts=(
            "Ajami script",
            "Nsibidi script",
            "Ge'ez script",
            "Tifinagh script",
            "Vai syllabary",
            "Adinkra symbols",
            "African writing systems history",
            "griot oral tradition",
            "Sundiata epic",
            "African oral literature",
            "Swahili poetry history",
            "Hausa literature history",
            "Yoruba literature history",
            "Igbo literature history",
            "Amharic literature history",
            "African proverbs study",
            "African folktales collection",
            "African praise poetry",
            "Ifa literary corpus",
            "African music history",
            "African drumming traditions",
            "kente cloth history",
            "adire textile Yoruba",
            "bogolanfini mud cloth",
            "African mask traditions",
            "African sculpture history",
            "African architecture vernacular",
            "Nubian language history",
            "Bantu languages classification",
            "Niger-Congo language family",
            "Afroasiatic languages",
            "Nilo-Saharan languages",
            "Khoisan languages",
            "African language documentation",
            "African linguistics research",
            "Swahili language history",
            "Hausa language history",
            "Yoruba language history",
            "African theatre history",
            "African cinema history",
            # Added 2026-09-28 when dorking saturated: every concept is
            # also a query for the repository harvesters.
            "Coptic language history",
            "Amazigh literature",
            "Wolof language history",
            "Fula language history",
            "Somali poetry tradition",
            "Zulu izibongo praise poetry",
            "Xhosa literature history",
            "Shona literature history",
            "Malagasy hainteny",
            "taarab music history",
            "highlife music history",
            "afrobeat Fela Kuti history",
            "juju music history",
            "mbira music Zimbabwe",
            "kora music griot",
            "African photography history",
            "Nollywood history",
            "African contemporary art history",
            "Gelede masquerade",
            "Egungun masquerade",
            "Makonde sculpture",
            "Tingatinga painting",
            "Ethiopian icon painting",
            "Arabic manuscripts West Africa",
            "African children's literature",
        ),
        dorks=HISTORY_DORKS,
    ),
)


# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #

LOGGER = logging.getLogger("dork_collector")


def configure_logging(level: str, log_file: Path) -> None:
    """Console + size-capped rotating file logging."""
    LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))
    LOGGER.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    LOGGER.addHandler(stream)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        LOGGER.addHandler(rotating)
    except OSError as exc:  # read-only filesystem, permissions, ...
        LOGGER.warning("File logging disabled (%s)", exc)

    # Third-party libraries are chatty at INFO; keep them at WARNING.
    for noisy in ("urllib3", "googleapiclient", "google.auth", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
#  Data model
# --------------------------------------------------------------------------- #

class Document(BaseModel):
    """One validated row destined for the sheet."""

    subject: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    hyperlink: str = Field(min_length=8, max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    open_source: bool
    authors: str = Field(default="", max_length=1000)  # "" = unknown

    @field_validator("subject", "source", "title", mode="before")
    @classmethod
    def _collapse_whitespace(cls, value: Any) -> str:
        """Sheets hates stray newlines/tabs; flatten everything to spaces."""
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text or "Unknown"

    @field_validator("hyperlink")
    @classmethod
    def _require_http(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError(f"not an http(s) URL: {value!r}")
        return value

    @field_validator("authors", mode="before")
    @classmethod
    def _flatten_authors(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def to_row(self) -> list[str]:
        """Sheet row in column order A..F."""
        return [
            self.subject,
            self.source,
            self.hyperlink,
            self.title,
            "TRUE" if self.open_source else "FALSE",
            self.authors,
        ]


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def jitter_sleep(low: float, high: float, stop: threading.Event | None = None) -> None:
    """Sleep a random interval, waking early if a shutdown was requested."""
    delay = random.uniform(low, high)
    LOGGER.debug("Sleeping %.1fs", delay)
    if stop is not None:
        stop.wait(delay)
    else:
        time.sleep(delay)


def normalise_url(url: str) -> str:
    """
    Canonicalise a URL so trivial variants collapse to one dedup key.

    Drops the fragment, strips tracking query parameters, lowercases the host
    and removes a trailing slash from non-root paths.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()

    if not parts.scheme or not parts.netloc:
        return url.strip()

    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    for port in (":80", ":443"):
        if netloc.endswith(port):
            netloc = netloc[: -len(port)]

    query = "&".join(
        part
        for part in parts.query.split("&")
        if part
        and not part.lower().startswith(("utm_", "fbclid", "gclid", "_ga", "ref="))
    )

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def registrable_domain(url: str) -> str:
    """
    Best-effort 'source' label for a URL, e.g. ``unesdoc.unesco.org``.

    Deliberately not a full public-suffix implementation -- we keep the host as
    given (minus ``www.``) because subdomains such as ``unesdoc.unesco.org`` and
    ``openknowledge.worldbank.org`` are genuinely useful provenance signals.
    """
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str, candidates: Iterable[str]) -> bool:
    """True if `host` equals, or is a subdomain of, any candidate domain."""
    return any(host == c or host.endswith("." + c) for c in candidates)


def is_open_access(url: str) -> bool:
    """
    Classify a URL as open access.

    Precedence: explicit paywalled publishers -> explicit open repositories ->
    open TLD heuristic -> False.
    """
    host = registrable_domain(url)
    if not host:
        return False
    if host_matches(host, CLOSED_ACCESS_DOMAINS):
        return False
    if host_matches(host, OPEN_ACCESS_DOMAINS):
        return True
    return host.endswith(OPEN_TLD_SUFFIXES)


#: Tokens shorter than this, and these words, carry no topical signal.
_RELEVANCE_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "from", "into", "past", "questions", "history"}
)
RELEVANCE_MIN_RATIO = 0.5


def concept_tokens(concept: str) -> list[str]:
    """Distinctive lowercase tokens for a concept, stopwords removed."""
    words = re.findall(r"[a-z0-9]+", concept.lower())
    tokens = [w for w in words if len(w) >= 3 and w not in _RELEVANCE_STOPWORDS]
    # If stripping left nothing (e.g. "Common Entrance past questions"), fall
    # back to the raw words so the gate still has something to match on.
    return tokens or [w for w in words if len(w) >= 3]


def is_relevant(concept: str, title: str, url: str, snippet: str = "") -> bool:
    """
    Does this document actually relate to the concept that surfaced it?

    Search backends honour `filetype:`/`site:` but not topicality, so a dork for
    "WAEC syllabus" happily returns an ERIC paper on Shakespeare. Such a row is
    worse than no row at all: column A would label it "WAEC syllabus", poisoning
    retrieval. We require at least half the concept's distinctive tokens to
    appear across the title, URL and search snippet.
    """
    tokens = concept_tokens(concept)
    if not tokens:
        return True

    haystack = " ".join(
        (
            title or "",
            re.sub(r"[^a-zA-Z0-9]+", " ", unquote(url or "")),
            snippet or "",
        )
    ).lower()

    hits = sum(1 for token in tokens if token in haystack)
    return (hits / len(tokens)) >= RELEVANCE_MIN_RATIO


def is_repository_item(url: str) -> bool:
    """True if the URL points at an individual document within a repository."""
    target = f"{urlparse(url).path}?{urlparse(url).query}"
    return any(pattern.search(target) for pattern in REPOSITORY_ITEM_PATTERNS)


def looks_promising(url: str) -> bool:
    """
    Cheap pre-filter applied before we spend an HTTP round-trip on a URL.

    Search backends honour ``filetype:pdf`` only loosely, so a raw result set
    still contains Wikipedia articles, Pinterest boards and the like. Anything
    that is neither PDF-shaped nor hosted on a known repository is dropped here
    rather than in `URLProcessor.verify`, which keeps outbound traffic (and the
    time budget) focused on plausible documents.
    """
    if not url.lower().startswith(("http://", "https://")):
        return False

    host = registrable_domain(url)
    if not host or any(frag in host for frag in BLOCKED_HOST_FRAGMENTS):
        return False

    path = urlparse(url).path.lower()
    if path.endswith(".pdf") or "/pdf" in path or "getpdf" in url.lower():
        return True

    # A repository landing page is worth a probe only when the URL names a
    # specific item - not for arbitrary pages that merely live on the host.
    return host_matches(host, OPEN_ACCESS_DOMAINS) and is_repository_item(url)


def clean_title(raw: str, url: str) -> str:
    """
    Turn a search-result / HTML title into something a human wants to read.

    Falls back to a prettified filename when the engine gives us nothing useful.
    """
    text = re.sub(r"\s+", " ", (raw or "")).strip()

    # Strip the leading "[PDF]" marker search engines prepend.
    text = re.sub(r"^\[?\s*PDF\s*\]?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" -|–—·:")

    # Strip known repository boilerplate off the tail, e.g.
    # "Africa in the 19th century : Ajayi : Internet Archive". Runs repeatedly
    # because sites chain several of these. Only an exact boilerplate match is
    # removed, so real subtitles after a colon survive.
    for _ in range(4):
        match = re.search(r"\s*[:|–—]\s*([^:|–—]+?)\s*$", text)
        if not match or match.group(1).strip().lower() not in TITLE_BOILERPLATE:
            break
        text = text[: match.start()].strip()

    # Drop trailing site branding, e.g. "... | UNESCO Digital Library".
    # Only pipes and en/em dashes qualify: a plain hyphen appears inside far too
    # many real titles ("... Western Sudan - A Review") to be safe here.
    if len(text) > 60:
        text = re.sub(r"\s*[|–—]\s*[^|–—]{0,40}$", "", text)

    # A search snippet that was truncated mid-title leaves a dangling ellipsis
    # (and often a half-finished author name) behind.
    text = re.sub(r"\s*[.…]{2,}\s*$", "", text)
    text = text.strip().strip(":|–—").strip()

    if len(text) < 5:
        stem = Path(unquote(urlparse(url).path)).stem
        stem = re.sub(r"[_\-+.]+", " ", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        text = stem.title() if stem else (registrable_domain(url) or "Untitled document")

    return text[:500]


# --------------------------------------------------------------------------- #
#  Sheets manager
# --------------------------------------------------------------------------- #

class SheetsManager:
    """
    Thin, retry-hardened wrapper around a single gspread worksheet.

    Owns the in-memory dedup set (built from column C at startup) and buffers
    rows so we spend one API call per `batch_size` documents instead of one per
    document -- Sheets' write quota is the binding constraint on this whole job.
    """

    def __init__(
        self,
        credentials_path: Path,
        sheet_id: str,
        worksheet_name: str = WORKSHEET_DEFAULT,
        batch_size: int = SHEET_BATCH_SIZE,
        dry_run: bool = False,
    ) -> None:
        self.credentials_path = credentials_path
        self.sheet_id = sheet_id
        self.worksheet_name = worksheet_name
        self.batch_size = max(1, batch_size)
        self.dry_run = dry_run

        self._lock = threading.Lock()
        self._buffer: list[list[str]] = []
        self._oldest_queued = 0.0  # monotonic time the buffer's first row arrived
        self._worksheet: gspread.Worksheet | None = None

        self.seen_urls: set[str] = set()
        self.rows_written = 0

        self._connect()
        self._load_existing_links()

    # -- connection ---------------------------------------------------------- #

    def _connect(self) -> None:
        """Authorise the service account and resolve the target worksheet."""
        LOGGER.info("Authorising service account from %s", self.credentials_path)
        creds = Credentials.from_service_account_file(
            str(self.credentials_path), scopes=SCOPES
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(self.sheet_id)

        try:
            self._worksheet = spreadsheet.worksheet(self.worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            LOGGER.warning(
                "Worksheet %r not found; falling back to the first tab",
                self.worksheet_name,
            )
            self._worksheet = spreadsheet.get_worksheet(0)

        if self._worksheet is None:
            raise RuntimeError("Spreadsheet contains no worksheets")

        LOGGER.info(
            "Connected to spreadsheet %r / worksheet %r",
            spreadsheet.title,
            self._worksheet.title,
        )
        self._ensure_header()

    @property
    def worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:  # pragma: no cover - defensive
            raise RuntimeError("Worksheet is not connected")
        return self._worksheet

    def _ensure_header(self) -> None:
        """Write the header row if the sheet is completely empty."""
        first_row = self._with_retry("read header", lambda: self.worksheet.row_values(1))
        if first_row:
            # Sheets created before a column existed get the missing labels
            # appended, never overwritten.
            if len(first_row) < len(HEADER_ROW) and first_row == HEADER_ROW[: len(first_row)]:
                self._write_header("extend header to " + HEADER_ROW[-1])
            return
        if self.dry_run:
            LOGGER.info("[dry-run] would write header row")
            return
        LOGGER.info("Sheet is empty - writing header row")
        self._write_header("write header")

    def _write_header(self, what: str) -> None:
        if self.dry_run:
            LOGGER.info("[dry-run] would %s", what)
            return
        last_col = chr(ord("A") + len(HEADER_ROW) - 1)
        self._with_retry(
            what,
            lambda: self.worksheet.update(
                values=[HEADER_ROW],
                range_name=f"A1:{last_col}1",
                value_input_option="USER_ENTERED",
            ),
        )

    def _load_existing_links(self) -> None:
        """Seed the dedup set from column C so restarts never re-append."""
        values = self._with_retry("read column C", lambda: self.worksheet.col_values(3)) or []
        for raw in values[1:]:  # skip the header cell
            url = normalise_url(str(raw))
            if url.lower().startswith(("http://", "https://")):
                self.seen_urls.add(url)
        LOGGER.info(
            "Loaded %d existing hyperlink(s) for deduplication", len(self.seen_urls)
        )

    # -- dedup --------------------------------------------------------------- #

    def is_new(self, url: str) -> bool:
        """Check membership without reserving the URL."""
        with self._lock:
            return normalise_url(url) not in self.seen_urls

    def reserve(self, url: str) -> bool:
        """
        Atomically claim a URL.

        Returns True exactly once per URL, so concurrent verification threads --
        or two queries in the same cycle -- never chase the same document twice.
        """
        key = normalise_url(url)
        with self._lock:
            if key in self.seen_urls:
                return False
            self.seen_urls.add(key)
            return True

    def release(self, url: str) -> None:
        """Undo a reservation when the document later fails validation."""
        with self._lock:
            self.seen_urls.discard(normalise_url(url))

    # -- writing ------------------------------------------------------------- #

    def queue(self, document: Document) -> None:
        """Buffer a row, flushing automatically once the batch is full."""
        with self._lock:
            if not self._buffer:
                self._oldest_queued = time.monotonic()
            self._buffer.append(document.to_row())
            ready = len(self._buffer) >= self.batch_size
        if ready:
            self.flush()

    def flush_if_stale(self, max_age: float = SHEET_MAX_ROW_AGE) -> int:
        """
        Flush a partial batch once its oldest row has waited `max_age` seconds.

        Late in a run almost every hit is a duplicate, so a batch of 8 can take
        hours to fill - rows sat invisible in memory and the sheet looked dead.
        """
        with self._lock:
            stale = bool(self._buffer) and (
                time.monotonic() - self._oldest_queued >= max_age
            )
        return self.flush() if stale else 0

    def flush(self) -> int:
        """
        Write everything currently buffered.

        The buffer is swapped out under the lock, so a failed write puts the
        rows back rather than losing them.
        """
        with self._lock:
            if not self._buffer:
                return 0
            batch, self._buffer = self._buffer, []

        if self.dry_run:
            for row in batch:
                LOGGER.info("[dry-run] row: %s", row)
            self.rows_written += len(batch)
            return len(batch)

        try:
            self._with_retry(
                f"append {len(batch)} row(s)",
                lambda: self.worksheet.append_rows(
                    batch,
                    value_input_option="USER_ENTERED",
                    insert_data_option="INSERT_ROWS",
                    table_range="A1",
                ),
            )
        except Exception:
            # Put the rows back at the front so ordering survives the retry.
            with self._lock:
                self._buffer = batch + self._buffer
                self._oldest_queued = time.monotonic()  # retry after max_age, not every query
            LOGGER.exception("Append failed; %d row(s) re-queued", len(batch))
            return 0

        self.rows_written += len(batch)
        LOGGER.info(
            "Appended %d row(s) (session total: %d)", len(batch), self.rows_written
        )
        return len(batch)

    # -- retry --------------------------------------------------------------- #

    def _with_retry(self, what: str, action):
        """
        Run a Sheets call with exponential backoff on 429/5xx.

        gspread raises `APIError` for everything, so we inspect the HTTP status
        to decide whether the failure is worth retrying.
        """
        delay = 5.0
        last_exc: Exception | None = None

        for attempt in range(1, SHEET_MAX_RETRIES + 1):
            try:
                return action()
            except gspread.exceptions.APIError as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                LOGGER.warning(
                    "Sheets %s failed (attempt %d/%d, status=%s): %s",
                    what, attempt, SHEET_MAX_RETRIES, status, exc,
                )
                if status not in (429, 500, 502, 503, 504):
                    raise  # 401/403/404 will not fix themselves
            except (
                requests.exceptions.RequestException,
                ConnectionError,
                TimeoutError,
            ) as exc:
                last_exc = exc
                LOGGER.warning(
                    "Sheets %s network error (attempt %d/%d): %s",
                    what, attempt, SHEET_MAX_RETRIES, exc,
                )

            if attempt < SHEET_MAX_RETRIES:
                sleep_for = delay + random.uniform(0, 5)
                LOGGER.info("Backing off %.1fs before retrying %s", sleep_for, what)
                time.sleep(sleep_for)
                delay = min(delay * 2, 120.0)

        assert last_exc is not None
        raise last_exc


# --------------------------------------------------------------------------- #
#  Search
# --------------------------------------------------------------------------- #

class SearchScraper:
    """
    DuckDuckGo-backed dork runner.

    DDG has no API key and no documented quota, which means the only thing
    standing between this script and a temporary IP ban is the backoff logic
    below. Treat `SEARCH_SLEEP_RANGE` as a floor, not a target.
    """

    def __init__(
        self,
        results_per_query: int = RESULTS_PER_QUERY,
        stop: threading.Event | None = None,
        backends: Sequence[str] = SEARCH_BACKENDS,
    ) -> None:
        self.results_per_query = results_per_query
        self.stop = stop
        self.backends = tuple(backends) or ("auto",)
        self.consecutive_failures = 0
        self._backend_cursor = 0

    def _pause(self, seconds: float) -> None:
        if self.stop is not None:
            self.stop.wait(seconds)
        else:
            time.sleep(seconds)

    def search(self, query: str, region: str = "wt-wt") -> list[dict[str, str]]:
        """
        Run one query, returning ``[{title, href, body}, ...]``.

        Never raises: a failed query yields an empty list so the service loop
        keeps moving.
        """
        backoff = SEARCH_BACKOFF_BASE

        for attempt in range(1, SEARCH_MAX_RETRIES + 1):
            if self.stop is not None and self.stop.is_set():
                return []

            # Rotate backends across attempts *and* across queries, so a single
            # unhappy provider neither blocks a query nor absorbs all traffic.
            backend = self.backends[
                (self._backend_cursor + attempt - 1) % len(self.backends)
            ]
            try:
                with DDGS(timeout=30) as ddgs:
                    kwargs: dict[str, Any] = {
                        _DDGS_QUERY_KWARG: query,
                        "region": region,
                        "safesearch": "off",
                        "max_results": self.results_per_query,
                    }
                    if _DDGS_MODERN:
                        # `backend` means something entirely different on the
                        # legacy package ("api"/"html"/"lite"), so only send it
                        # when we know we are talking to `ddgs`.
                        kwargs["backend"] = backend
                    try:
                        raw = list(ddgs.text(**kwargs))
                    except TypeError:
                        # Package versions disagree on the argument names; fall
                        # back to the lowest common denominator.
                        raw = list(ddgs.text(query, max_results=self.results_per_query))

                results = [
                    {
                        "title": str(item.get("title") or ""),
                        "href": str(item.get("href") or item.get("url") or ""),
                        "body": str(item.get("body") or ""),
                    }
                    for item in raw
                    if item.get("href") or item.get("url")
                ]
                if not results:
                    # An empty set is not an error, but it usually means this
                    # backend is unhappy - let the next attempt try another one.
                    raise RuntimeError("no results returned")

                self.consecutive_failures = 0
                self._backend_cursor += 1
                LOGGER.info(
                    "Query %r via %s -> %d result(s)", query, backend, len(results)
                )
                return results

            except Exception as exc:  # DDG surfaces a zoo of exception types
                message = str(exc).lower()
                rate_limited = any(
                    token in message
                    for token in ("ratelimit", "rate limit", "202", "403", "429", "timeout")
                )
                LOGGER.warning(
                    "Search via %s failed (attempt %d/%d)%s: %s",
                    backend,
                    attempt,
                    SEARCH_MAX_RETRIES,
                    " [rate limited]" if rate_limited else "",
                    exc,
                )
                if attempt < SEARCH_MAX_RETRIES:
                    if "no results" in message:
                        # Not a throttle - just a backend with nothing to say.
                        # Switch provider after a token pause instead of
                        # burning minutes of exponential backoff.
                        sleep_for = random.uniform(2.0, 5.0)
                    else:
                        sleep_for = backoff + random.uniform(0, backoff * 0.5)
                        backoff = min(backoff * 2, SEARCH_BACKOFF_MAX)
                    LOGGER.info("Backing off %.1fs", sleep_for)
                    self._pause(sleep_for)

        self.consecutive_failures += 1
        LOGGER.error("Giving up on query %r after %d attempts", query, SEARCH_MAX_RETRIES)
        return []

    def cool_down_if_struggling(self) -> None:
        """
        Extended pause once several queries in a row have failed outright.

        This is the 'we are probably soft-banned' circuit breaker.
        """
        if self.consecutive_failures < 3:
            return
        penalty = min(300.0 * self.consecutive_failures, 1800.0)
        LOGGER.warning(
            "%d consecutive query failures - cooling down for %.0fs",
            self.consecutive_failures,
            penalty,
        )
        self._pause(penalty)


# --------------------------------------------------------------------------- #
#  URL verification
# --------------------------------------------------------------------------- #

class URLProcessor:
    """
    Verifies that a candidate URL is a live, reachable document and derives the
    metadata for its sheet row.

    Strategy: HEAD first (cheap), fall back to a ranged GET when the server
    rejects HEAD or hides the content type -- a surprising number of academic
    repositories do exactly that.
    """

    PDF_CONTENT_TYPES = ("application/pdf", "application/x-pdf", "application/octet-stream")
    HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

    def __init__(self, timeout: tuple[int, int] = VERIFY_TIMEOUT) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=VERIFY_WORKERS * 2,
            pool_maxsize=VERIFY_WORKERS * 2,
            max_retries=0,  # retries are handled a level up
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _headers(self) -> dict[str, str]:
        """Fresh browser-ish headers, rotating the User-Agent each call."""
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    @staticmethod
    def _looks_like_pdf_url(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".pdf") or "/pdf" in path or "getpdf" in url.lower()

    def _is_acceptable(self, url: str, content_type: str, allow_html: bool = False) -> bool:
        """
        Accept PDFs outright; accept HTML only from known open repositories,
        where a landing page almost always fronts a real document.

        `allow_html` is for URLs that came from a repository's own catalogue
        rather than a search engine: the repository has already vouched that
        this is a document record, so its landing page is acceptable even
        though the host is not on our list.
        """
        ctype = content_type.split(";")[0].strip().lower()

        if ctype in self.PDF_CONTENT_TYPES:
            # octet-stream is only trustworthy when the URL also looks like a PDF
            if ctype == "application/octet-stream":
                return self._looks_like_pdf_url(url)
            return True

        if not ctype and self._looks_like_pdf_url(url):
            return True

        if ctype in self.HTML_CONTENT_TYPES:
            if allow_html:
                return True
            # Landing pages only, and only for a named item - see
            # REPOSITORY_ITEM_PATTERNS for why the host check alone is not enough.
            return host_matches(
                registrable_domain(url), OPEN_ACCESS_DOMAINS
            ) and is_repository_item(url)

        return False

    def verify(self, url: str, allow_html: bool = False) -> dict[str, Any] | None:
        """
        Probe a URL.

        Returns ``{"url", "content_type", "html"}`` on success (``html`` is the
        decoded body only when the response was HTML), or ``None`` when the URL
        should be discarded.
        """
        host = registrable_domain(url)
        if not host or any(frag in host for frag in BLOCKED_HOST_FRAGMENTS):
            LOGGER.debug("Skipping blocked host: %s", url)
            return None

        try:
            response = self.session.head(
                url, headers=self._headers(), timeout=self.timeout, allow_redirects=True
            )

            # Many repositories answer HEAD with 403/405 but serve GET fine.
            if response.status_code in (400, 403, 405, 501) or not response.headers.get(
                "Content-Type"
            ):
                response = self._ranged_get(url)
            elif response.status_code != 200:
                LOGGER.debug("HEAD %s -> HTTP %s", url, response.status_code)
                return None

            if response is None:
                return None
            if response.status_code != 200:
                LOGGER.debug("GET %s -> HTTP %s", url, response.status_code)
                response.close()
                return None

            content_type = response.headers.get("Content-Type", "")
            final_url = str(response.url) or url

            if not self._is_acceptable(final_url, content_type, allow_html):
                LOGGER.debug("Rejected %s (content-type=%r)", final_url, content_type)
                response.close()
                return None

            html = ""
            if content_type.split(";")[0].strip().lower() in self.HTML_CONTENT_TYPES:
                html = self._read_html(response, final_url)
            else:
                response.close()

            return {"url": final_url, "content_type": content_type, "html": html}

        except requests.exceptions.RequestException as exc:
            LOGGER.debug("Verification failed for %s: %s", url, exc)
            return None
        except Exception as exc:  # malformed URLs, SSL oddities, decode errors
            LOGGER.debug("Unexpected verification error for %s: %s", url, exc)
            return None

    def _ranged_get(self, url: str) -> requests.Response | None:
        """Streamed GET asking for only the first slice of the body."""
        headers = self._headers()
        headers["Range"] = "bytes=0-65535"
        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=True,
                stream=True,
            )
            # 206 Partial Content is a success for our purposes.
            if response.status_code == 206:
                response.status_code = 200
            return response
        except requests.exceptions.RequestException as exc:
            LOGGER.debug("Ranged GET failed for %s: %s", url, exc)
            return None

    def _read_html(self, response: requests.Response, url: str) -> str:
        """Pull at most 128 KB of an HTML body -- we only want the <head>."""
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= 128 * 1024:
                    break
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")
        except Exception as exc:
            LOGGER.debug("Could not read HTML body for %s: %s", url, exc)
            return ""
        finally:
            response.close()

    @staticmethod
    def extract_pdf_link(html: str) -> str:
        """
        The PDF behind a landing page, from its `citation_pdf_url` tag.

        Repository catalogues hand out landing pages; journals and DSpace both
        advertise the actual file here, so a harvested row can point at the
        document instead of the page about it.
        """
        match = re.search(
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
            html or "", re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
            html or "", re.IGNORECASE,
        )
        url = (match.group(1).strip() if match else "")
        return url if url.lower().startswith(("http://", "https://")) else ""

    @staticmethod
    def extract_html_title(html: str) -> str:
        """
        Pull a title out of an HTML head.

        Prefers citation / Dublin Core / OpenGraph metadata over ``<title>``,
        because repository landing pages put the real document title there.
        """
        if not html:
            return ""

        meta_patterns = (
            r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_title["\']',
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']DC\.title["\'][^>]+content=["\']([^"\']+)["\']',
        )
        for pattern in meta_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()

        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"<[^>]+>", " ", match.group(1))

        return ""

    def build_document(
        self, subject: str, result: dict[str, str], verified: dict[str, Any]
    ) -> Document | None:
        """Assemble a validated `Document` from a search hit + probe result."""
        url = verified["url"]
        raw_title = self.extract_html_title(verified.get("html", "")) or result.get(
            "title", ""
        )
        title = clean_title(raw_title, url)

        # Column A labels the row with `subject`, so an off-topic document is
        # actively mislabeled data - drop it rather than store it.
        if not is_relevant(subject, title, url, result.get("body", "")):
            LOGGER.debug("Discarding %s - not relevant to %r", url, subject)
            return None

        try:
            return Document(
                subject=subject,
                source=registrable_domain(url),
                hyperlink=url,
                title=title,
                open_source=is_open_access(url),
            )
        except Exception as exc:  # pydantic ValidationError and friends
            LOGGER.debug("Discarding %s - failed validation: %s", url, exc)
            return None

    def close(self) -> None:
        self.session.close()


# --------------------------------------------------------------------------- #
#  Query planning + state
# --------------------------------------------------------------------------- #

@dataclass
class RunState:
    """Small JSON-backed counter set so restarts do not repeat the same work."""

    path: Path
    cycle: int = 0
    total_found: int = 0
    total_queries: int = 0
    harvest: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def load(cls, path: Path) -> "RunState":
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(
                    path=path,
                    cycle=int(data.get("cycle", 0)),
                    total_found=int(data.get("total_found", 0)),
                    total_queries=int(data.get("total_queries", 0)),
                    harvest=data.get("harvest") or {},
                    started_at=str(
                        data.get("started_at", datetime.now(timezone.utc).isoformat())
                    ),
                )
            except (OSError, ValueError, TypeError) as exc:
                LOGGER.warning("Could not read state file (%s); starting fresh", exc)
        return cls(path=path)

    def save(self) -> None:
        payload = {
            "cycle": self.cycle,
            "total_found": self.total_found,
            "total_queries": self.total_queries,
            "harvest": self.harvest,
            "started_at": self.started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)  # atomic on POSIX
        except OSError as exc:
            LOGGER.warning("Could not persist state: %s", exc)


class QueryPlanner:
    """
    Decides which dork to fire next.

    Each cycle reshuffles the (concept x dork) matrix with a cycle-seeded RNG,
    so coverage is complete but the ordering differs every pass -- which both
    varies the traffic pattern and surfaces different results over time.
    """

    def __init__(self, categories: Sequence[Category] = CATEGORIES) -> None:
        self.categories = list(categories)

    def plan(self, cycle: int) -> Iterator[tuple[str, str, str]]:
        """Yield ``(category_name, concept, query)`` for one full pass."""
        rng = random.Random(cycle * 7919)
        categories = list(self.categories)
        rng.shuffle(categories)

        for category in categories:
            pairs = category.queries()
            rng.shuffle(pairs)
            for concept, query in pairs:
                yield category.name, concept, self._vary(query, cycle, rng)

    @staticmethod
    def _vary(query: str, cycle: int, rng: random.Random) -> str:
        """
        Add a rotating qualifier so repeated cycles reach different results.

        DDG's text endpoint exposes no page offset we can drive directly, so we
        perturb the query itself instead.
        """
        # The empty string appears twice on purpose: the bare dork is the single
        # most productive form, so it should come up more often than any one
        # qualifier while the rest widen coverage across repeat passes.
        modifiers = (
            "",
            "",
            "pdf",
            "download",
            "full text",
            "chapter",
            "journal article",
            "research paper",
            "review",
            "study",
            "overview",
            "introduction",
        )
        modifier = modifiers[(cycle + rng.randrange(len(modifiers))) % len(modifiers)]
        return f"{query} {modifier}".strip()


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #

class Collector:
    """Wires the components together and owns the long-running service loop."""

    def __init__(
        self,
        sheets: SheetsManager,
        scraper: SearchScraper,
        processor: URLProcessor,
        state: RunState,
        stop: threading.Event,
        workers: int = VERIFY_WORKERS,
        max_queries: int | None = None,
        authors: AuthorResolver | None = None,
        harvester: HarvestPlanner | None = None,
    ) -> None:
        self.sheets = sheets
        self.scraper = scraper
        self.processor = processor
        self.state = state
        self.stop = stop
        self.workers = max(1, workers)
        # Cap on queries per cycle. Only used for smoke tests - a full cycle is
        # ~160 queries, which is far too slow to verify a deployment with.
        self.max_queries = max_queries
        self.planner = QueryPlanner()
        # None disables author lookup (--no-authors); column F is left blank.
        self.authors = authors
        # None disables repository harvesting (--no-harvest).
        self.harvester = harvester

    def _evaluate(self, concept: str, item: dict[str, str]) -> Document | None:
        """
        Worker-thread body: verify the URL, build its row, find its authors.

        Author lookup runs here, after the relevance gate, so we only spend API
        calls (and possibly a PDF download) on documents we are going to keep.
        """
        verified = self.processor.verify(item["href"])
        if verified is None:
            return None
        document = self.processor.build_document(concept, item, verified)
        if document is None or self.authors is None:
            return document
        found = self.authors.resolve(
            document.hyperlink, document.title, verified.get("html", "")
        )
        if found:
            document.authors = found.text
            LOGGER.debug("Authors via %s: %s", found.method, found.text)
        return document

    def handle_query(self, category: str, concept: str, query: str) -> int:
        """
        Run one dork end to end.

        Returns the number of new rows queued for the sheet.
        """
        LOGGER.info("[%s] %s", category, query)
        results = self.scraper.search(query)
        self.state.total_queries += 1
        if not results:
            return 0

        # Drop the obvious non-documents, then reserve what is left up front so
        # no two workers chase the same URL.
        pending: list[dict[str, str]] = []
        skipped = 0
        for result in results:
            url = result.get("href", "")
            if not looks_promising(url):
                skipped += 1
                continue
            if self.sheets.reserve(url):
                pending.append(result)

        if not pending:
            LOGGER.info(
                "Nothing new from %d result(s) (%d pre-filtered)", len(results), skipped
            )
            return 0

        LOGGER.info("Verifying %d new candidate(s)", len(pending))
        added = 0

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._evaluate, concept, item): item for item in pending
            }
            for future in as_completed(futures):
                item = futures[future]
                url = item["href"]
                try:
                    document = future.result()
                except Exception as exc:  # a worker blew up unexpectedly
                    LOGGER.debug("Worker error for %s: %s", url, exc)
                    document = None

                if document is None:
                    self.sheets.release(url)
                    continue

                # The probe may have redirected us somewhere new; claim that too.
                if normalise_url(document.hyperlink) != normalise_url(url):
                    if not self.sheets.reserve(document.hyperlink):
                        self.sheets.release(url)
                        continue

                self.sheets.queue(document)
                added += 1
                LOGGER.info(
                    "  + %s | %s | open=%s | by=%s",
                    document.source,
                    document.title[:80],
                    document.open_source,
                    document.authors[:60] or "?",
                )

        self.state.total_found += added
        return added

    def harvest_step(self) -> int:
        """
        Pull one page from the next repository and queue whatever is new.

        Harvested URLs skip `looks_promising`: that filter exists to guess which
        *search results* might be documents, and a repository catalogue already
        knows. They still get verified over HTTP and deduplicated.
        """
        if self.harvester is None:
            return 0
        records = self.harvester.step()
        if not records:
            return 0

        pending = [r for r in records if self.sheets.reserve(r.url)]
        if not pending:
            LOGGER.info("Harvest: nothing new in %d record(s)", len(records))
            return 0

        LOGGER.info("Harvest: verifying %d new candidate(s)", len(pending))
        added = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._evaluate_harvested, r): r for r in pending}
            for future in as_completed(futures):
                record = futures[future]
                try:
                    document = future.result()
                except Exception as exc:
                    LOGGER.debug("Harvest worker error for %s: %s", record.url, exc)
                    document = None

                if document is None:
                    self.sheets.release(record.url)
                    continue
                if normalise_url(document.hyperlink) != normalise_url(record.url):
                    if not self.sheets.reserve(document.hyperlink):
                        self.sheets.release(record.url)
                        continue

                self.sheets.queue(document)
                added += 1
                LOGGER.info(
                    "  + [%s] %s | %s | by=%s",
                    record.source, document.source, document.title[:70],
                    document.authors[:50] or "?",
                )

        self.state.total_found += added
        return added

    def _evaluate_harvested(self, record: HarvestRecord) -> Document | None:
        """Verify one harvested record and turn it into a row."""
        verified = self.processor.verify(record.url, allow_html=True)
        if verified is None:
            return None

        # A landing page usually advertises the file itself; prefer that.
        html = verified.get("html", "")
        if html:
            pdf_url = self.processor.extract_pdf_link(html)
            if pdf_url and normalise_url(pdf_url) != normalise_url(verified["url"]):
                better = self.processor.verify(pdf_url)
                if better is not None:
                    verified = better

        url = verified["url"]
        title = clean_title(
            record.title or self.processor.extract_html_title(verified.get("html", "")), url
        )
        # Fuzzy sources (keyword relevance, not a phrase match) can drift
        # off-topic, and column A would then mislabel the row. `is_relevant`
        # is too generous here - half the tokens of "Nok culture" is just
        # "nok", which is a common Danish word - so require the whole phrase.
        if record.needs_relevance_check and not (
            phrase_in_text(record.concept, title) or is_relevant(record.concept, title, url)
        ):
            LOGGER.debug("Harvest: %s not relevant to %r", url, record.concept)
            return None

        # Catalogues sometimes list a journal or an institution as an author
        # ("10 Chemistry"), so the harvested names go through the same gate as
        # the resolver's.
        authors = format_authors([
            name for name in (clean_name(a) for a in record.authors if str(a).strip())
            if is_plausible_creator(name)
        ])
        if not authors and self.authors is not None:
            found = self.authors.resolve(url, title, verified.get("html", ""))
            authors = found.text

        try:
            return Document(
                subject=record.concept,
                source=registrable_domain(url),
                hyperlink=url,
                title=title,
                open_source=is_open_access(url),
                authors=authors,
            )
        except Exception as exc:
            LOGGER.debug("Harvest: discarding %s - %s", url, exc)
            return None

    def run_cycle(self) -> int:
        """One complete pass over every category. Returns rows added."""
        self.state.cycle += 1
        cycle = self.state.cycle
        LOGGER.info("=" * 72)
        LOGGER.info("Starting cycle %d", cycle)
        LOGGER.info("=" * 72)

        added = 0
        issued = 0
        current_category: str | None = None

        for category, concept, query in self.planner.plan(cycle):
            if self.stop.is_set():
                break
            if self.max_queries is not None and issued >= self.max_queries:
                LOGGER.info("Reached --max-queries limit of %d", self.max_queries)
                break
            issued += 1

            # Breather when we roll into a new category.
            if current_category is not None and category != current_category:
                LOGGER.info("Switching category -> %s", category)
                jitter_sleep(*CATEGORY_SLEEP_RANGE, stop=self.stop)
            current_category = category

            try:
                added += self.handle_query(category, concept, query)
            except Exception:
                # A single bad query must never take down a 3-day run.
                LOGGER.exception("Unhandled error on query %r", query)

            if self.harvester is not None and issued % HARVEST_EVERY_QUERIES == 0:
                try:
                    added += self.harvest_step()
                except Exception:
                    LOGGER.exception("Unhandled error while harvesting")
                self.state.harvest = self.harvester.dump()

            self.state.save()
            self.sheets.flush_if_stale()
            self.scraper.cool_down_if_struggling()

            if not self.stop.is_set():
                jitter_sleep(*SEARCH_SLEEP_RANGE, stop=self.stop)

        self.sheets.flush()
        LOGGER.info(
            "Cycle %d complete: +%d row(s) | session total %d | queries %d",
            cycle,
            added,
            self.sheets.rows_written,
            self.state.total_queries,
        )
        self.state.save()
        return added

    def run_harvest_forever(self) -> None:
        """Work through the repositories only, without searching at all."""
        LOGGER.info("Harvest-only mode: %s", self.harvester.summary() if self.harvester else "-")
        idle = 0
        try:
            while not self.stop.is_set():
                added = self.harvest_step()
                self.state.harvest = self.harvester.dump() if self.harvester else {}
                self.state.save()
                self.sheets.flush_if_stale()
                # Every source resting or spent: wait before trying again.
                idle = 0 if added else idle + 1
                if idle >= 5:
                    LOGGER.info("Nothing to harvest right now; resting")
                    jitter_sleep(*CATEGORY_SLEEP_RANGE, stop=self.stop)
                    idle = 0
                else:
                    jitter_sleep(2.0, 6.0, stop=self.stop)
        finally:
            LOGGER.info("Shutting down - flushing buffered rows")
            self.sheets.flush()
            self.processor.close()
            self.state.save()

    def run_forever(self, once: bool = False) -> None:
        """The service loop. Exits only on shutdown signal, or when `once`."""
        try:
            while not self.stop.is_set():
                self.run_cycle()
                if once or self.stop.is_set():
                    break
                LOGGER.info("Resting between cycles...")
                jitter_sleep(*CYCLE_SLEEP_RANGE, stop=self.stop)
        finally:
            LOGGER.info("Shutting down - flushing buffered rows")
            self.sheets.flush()
            self.processor.close()
            self.state.save()
            LOGGER.info(
                "Final tally: %d row(s) written across %d cycle(s), %d quer(ies)",
                self.sheets.rows_written,
                self.state.cycle,
                self.state.total_queries,
            )


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def discover_credentials(explicit: str | None) -> Path:
    """
    Locate the service-account key.

    Order: --credentials, $GOOGLE_APPLICATION_CREDENTIALS, service_account.json
    beside the script / one directory up / in the CWD, then any *.json in those
    directories whose ``type`` is ``service_account``.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env:
        candidates.append(Path(env).expanduser())

    search_dirs = [BASE_DIR, BASE_DIR.parent, Path.cwd()]
    candidates.extend(d / "service_account.json" for d in search_dirs)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("type") == "service_account":
                return path.resolve()

    raise FileNotFoundError(
        "No service account key found. Pass --credentials /path/to/key.json or "
        "set GOOGLE_APPLICATION_CREDENTIALS."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dork for open-access PDFs and auto-populate a Google Sheet.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--credentials", default=None, help="Path to the service account JSON key."
    )
    parser.add_argument(
        "--sheet-id",
        default=os.getenv("SHEET_ID", SHEET_ID_DEFAULT),
        help="Target spreadsheet ID.",
    )
    parser.add_argument(
        "--worksheet",
        default=os.getenv("WORKSHEET_NAME", WORKSHEET_DEFAULT),
        help="Target worksheet/tab name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=SHEET_BATCH_SIZE,
        help="Rows buffered before each sheet write.",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=RESULTS_PER_QUERY,
        help="Max search results requested per query.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=VERIFY_WORKERS,
        help="Concurrent URL verification threads.",
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Stop each cycle after this many queries. For smoke tests: a full "
        "cycle is ~160 queries and takes roughly half an hour.",
    )
    parser.add_argument(
        "--no-harvest",
        action="store_true",
        help="Do not harvest repository APIs; search engines only.",
    )
    parser.add_argument(
        "--harvest-only",
        action="store_true",
        help="Harvest repositories and never search. Much higher yield per "
        "request; use it to work through a newly added source.",
    )
    parser.add_argument(
        "--no-oai",
        action="store_true",
        help="Skip OAI-PMH repositories (they are walked wholesale and filtered).",
    )
    parser.add_argument(
        "--no-authors",
        action="store_true",
        help="Skip author lookup (column F stays blank). Author lookup uses "
        "OPENAI_API_KEY, when set, for PDFs no metadata source can attribute.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log rows instead of writing them to the sheet.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console/file log verbosity.",
    )
    parser.add_argument(
        "--log-file",
        default=str(BASE_DIR / "logs" / "collector.log"),
        help="Rotating log file path.",
    )
    parser.add_argument(
        "--state-file",
        default=str(BASE_DIR / "state.json"),
        help="Where cycle counters are persisted.",
    )
    return parser.parse_args(argv)


def install_signal_handlers(stop: threading.Event) -> None:
    """Turn SIGINT/SIGTERM into a clean, buffer-flushing shutdown."""

    def handler(signum: int, _frame: Any) -> None:
        LOGGER.warning("Received signal %s - finishing current work then exiting", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):  # non-main thread / Windows
            pass


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, Path(args.log_file))

    LOGGER.info("dork_sheet_collector starting up")
    stop = threading.Event()
    install_signal_handlers(stop)

    try:
        credentials_path = discover_credentials(args.credentials)
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        return 2

    try:
        sheets = SheetsManager(
            credentials_path=credentials_path,
            sheet_id=args.sheet_id,
            worksheet_name=args.worksheet,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    except Exception:
        LOGGER.exception("Could not connect to Google Sheets")
        return 3

    scraper = SearchScraper(results_per_query=args.results, stop=stop)
    processor = URLProcessor()
    state = RunState.load(Path(args.state_file))
    authors = None if args.no_authors else AuthorResolver()
    if authors is not None:
        LOGGER.info(
            "Author lookup enabled (LLM fallback %s)", "on" if authors.use_llm else "off"
        )

    harvester = None
    if not args.no_harvest:
        concepts = [c for category in CATEGORIES for c in category.concepts]
        harvester = HarvestPlanner(
            concepts=concepts,
            match_concept=concept_matcher(concepts),
            state=state.harvest,
            semantic_scholar_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
            core_key=os.getenv("CORE_API_KEY", ""),
            enable_oai=not args.no_oai,
        )
        LOGGER.info(
            "Harvesting %d source(s) over %d concepts",
            len(harvester.states), len(concepts),
        )

    collector = Collector(
        sheets=sheets,
        scraper=scraper,
        processor=processor,
        state=state,
        stop=stop,
        workers=args.workers,
        max_queries=args.max_queries,
        authors=authors,
        harvester=harvester,
    )

    try:
        if args.harvest_only:
            collector.run_harvest_forever()
        else:
            collector.run_forever(once=args.once)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user")
    except Exception:
        LOGGER.exception("Fatal error in service loop")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
