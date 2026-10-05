"""Tests for scripts/Apply-WebsiteReactTemplate.ps1 on config-driven templates.

701's content job runs this script against the freshly created repo. Both
current FFC templates (FFC-IN-Footer_Only_Template, the default, and
FFC-IN-FFC_Single_Page_Template) render the footer from `siteConfig` in
src/lib/site.config.ts and type team members as { name, role, linkedinUrl }.
The script's older regex footer patch matched nothing there and wrote
{ title, imageUrl } team JSON that fails the TypeScript build, while the
content step still reported success. These tests pin the config-driven path
against a fixture repo shaped like the templates, so no network is needed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "Apply-WebsiteReactTemplate.ps1"

SITE_CONFIG = """export type SiteConfig = {
  name: string
  parentOrg?: { name: string; url: string; hubUrl: string }
}

export const siteConfig: SiteConfig = {
  name: 'Free For Charity',
  tagline: 'Reduce Costs, Increase Impact',
  mission:
    'Free For Charity connects students, professionals, and businesses with nonprofits.',
  // Empty = the footer's Donate / Volunteer links email contactEmail instead.
  donationUrl: '',
  volunteerUrl: '',
  description:
    'Free For Charity connects students {with braces} and "quotes" to nonprofits.',
  shortDescription: 'Connecting students with nonprofits.',
  url: 'https://ffcworkingsite1.org',
  twitterHandle: '@freeforcharity',
  contactEmail: 'clarkemoyer@freeforcharity.org',
  keywords: ['nonprofit', 'charity'],
  social: [
    { label: 'Facebook', href: 'https://www.facebook.com/freeforcharity' },
    { label: 'X (Twitter)', href: 'https://x.com/freeforcharity1' },
    { label: 'LinkedIn', href: 'https://www.linkedin.com/company/freeforcharity/' },
    // Repo name uses underscores, the hyphenated variant 404s.
    { label: 'GitHub', href: 'https://github.com/FreeForCharity/FFC-IN-Footer_Only_Template' },
  ],
  ein: '46-2471893',
  phone: { display: '(520) 222-8104', tel: '5202228104' },
  addresses: [
    {
      label: 'Main Address',
      lines: ['4030 Wake Forrest Road', 'Raleigh NC 27609'],
      mapUrl: 'https://www.google.com/maps/search/?api=1&query=4030+Wake+Forrest',
    },
    {
      label: 'PA Office Address',
      lines: ['301 Science Park Road Suite', '119 State College PA 16803'],
      mapUrl: 'https://www.google.com/maps/place/Free+For+Charity/@40.7768455,-77.8963305,17z',
    },
  ],
  foundingDate: '2014',
  nonprofitStatus: 'https://schema.org/Nonprofit501c3',
  taxStatusLabel: 'a US 501c3 Non Profit',
  guidestar: {
    profileUrl: 'https://www.guidestar.org/profile/46-2471893',
    directProfileUrl: 'https://www.guidestar.org/profile/shared/bbbe173a',
  },
  supportedBy: {
    name: 'Free For Charity',
    url: 'https://freeforcharity.org',
    hubUrl: 'https://freeforcharity.org/hub/',
  },
  parentOrg: {
    name: 'Free For Charity',
    url: 'https://freeforcharity.org',
    hubUrl: 'https://freeforcharity.org/hub/',
  },
}

export function sitePath(path = '/'): string {
  return path
}
"""

# Shaped like the Single Page template's team.ts, which exports more than
# `team`: those extra exports must survive the rewrite.
TEAM_TS = """// Team member data

import clarkeMoyer from './team/clarke-moyer.json'
import chrisRae from './team/chris-rae.json'

export type TeamMember = {
  name: string
  role: string
  linkedinUrl?: string
}

export const team: TeamMember[] = [
  clarkeMoyer,
  chrisRae,
]

export const configuredTeam: TeamMember[] = team.filter((member) => member.name.trim())
"""

SECURITY_TXT = "Contact: mailto:clarkemoyer@freeforcharity.org\nExpires: 2027-01-01T00:00:00.000Z\n"

FULL_ARGS = {
    "Domain": "helpinghands.org",
    "CharityName": "St. Mary's Shelter",
    "FooterEmail": "info@helpinghands.org",
    "FooterPhone": "(555) 123-4567",
    "FooterAddress": "12 Main St\nSpringfield, IL 62701",
    "FooterEin": "12-3456789",
    "GuideStarProfileUrl": "",
    "GuideStarDirectProfileUrl": "",
    "FooterSocial": [
        "Facebook: https://www.facebook.com/helpinghands",
        "X: https://x.com/helpinghands",
    ],
    "LeadershipLines": [
        "President - Jane O'Doe",
        "Jim Roe | Treasurer | https://www.linkedin.com/in/jimroe",
    ],
    "Mission": "We shelter families.\nEvery night.",
    "DonationUrl": "https://www.zeffy.com/donate/helping-hands",
    "VolunteerUrl": "http://not-https.example.org",
    "IrsStatus": "501(c)(3) (approved)",
}


def make_repo(td: pathlib.Path, site_config: str = SITE_CONFIG) -> pathlib.Path:
    repo = td / "repo"
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "data" / "team").mkdir(parents=True)
    (repo / "public" / ".well-known").mkdir(parents=True)
    (repo / "src" / "lib" / "site.config.ts").write_text(site_config, encoding="utf-8", newline="\n")
    (repo / "src" / "data" / "team.ts").write_text(TEAM_TS, encoding="utf-8", newline="\n")
    # The template's sample team is FFC's own people.
    for slug, name in (("clarke-moyer", "Clarke Moyer"), ("chris-rae", "Chris Rae")):
        (repo / "src" / "data" / "team" / f"{slug}.json").write_text(
            json.dumps({"name": name, "role": "Free For Charity Board"}) + "\n", encoding="utf-8"
        )
    for rel in ("public/security.txt", "public/.well-known/security.txt"):
        (repo / rel).write_text(SECURITY_TXT, encoding="utf-8", newline="\n")
    return repo


def ps_literal(value) -> str:
    if isinstance(value, list):
        return "@(" + ", ".join(ps_literal(v) for v in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def run_apply(repo: pathlib.Path, args: dict) -> subprocess.CompletedProcess:
    # Called the way 701 calls it: in-process, with real string arrays. `pwsh
    # -File` would flatten an array argument into one string.
    params = " ".join(f"-{k} {ps_literal(v)}" for k, v in args.items())
    # The wrapper lives in its own temp dir, never beside the repo: workflow 748
    # applies to a checkout inside the Actions workspace.
    with tempfile.TemporaryDirectory() as wd:
        wrapper = pathlib.Path(wd) / "invoke.ps1"
        wrapper.write_text(
            f"& {ps_literal(str(SCRIPT))} -RepoPath {ps_literal(str(repo))} {params}\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        # Generous: against a real template the script runs the repo's pinned
        # prettier through npx, which may download it first.
        return subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
            env=child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=900,
        )


def applied(args: dict = FULL_ARGS, site_config: str = SITE_CONFIG):
    td = pathlib.Path(tempfile.mkdtemp())
    repo = make_repo(td, site_config)
    proc = run_apply(repo, args)
    return td, repo, proc


def read(repo: pathlib.Path, rel: str) -> str:
    return (repo / rel).read_bytes().decode("utf-8")


def test_writes_the_charity_identity_into_site_config():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Config-driven template detected" in proc.stdout, proc.stdout
        cfg = read(repo, "src/lib/site.config.ts")
        assert "name: 'St. Mary\\'s Shelter'," in cfg, cfg
        # Multi-line input collapses to the single footer sentence, and the
        # template's FFC description is replaced by it too.
        # mission and shortDescription carry it verbatim; description extends
        # it, because the templates require a description over 50 characters.
        assert cfg.count("'We shelter families. Every night.'") == 2, cfg
        assert "description: 'We shelter families. Every night. Learn about" in cfg, cfg
        assert "contactEmail: 'info@helpinghands.org'," in cfg, cfg
        assert "ein: '12-3456789'," in cfg, cfg
        assert "phone: { display: '(555) 123-4567', tel: '5551234567' }," in cfg, cfg
        assert "lines: ['12 Main St', 'Springfield, IL 62701']," in cfg, cfg
        assert "twitterHandle: '@helpinghands'," in cfg, cfg
        assert "{ label: 'X (Twitter)', href: 'https://x.com/helpinghands' }," in cfg, cfg
        # Nothing of FFC's identity remains outside the permanent attribution.
        assert "46-2471893" not in cfg and "clarkemoyer@" not in cfg, cfg
        assert "freeforcharity1" not in cfg and "(520) 222-8104" not in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_donate_and_volunteer_urls_only_accept_https():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "donationUrl: 'https://www.zeffy.com/donate/helping-hands'," in cfg, cfg
        # Not https: left blank, so the template's footer link emails the charity.
        assert "volunteerUrl: ''," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_blank_mission_becomes_a_sentence_naming_the_charity():
    td, repo, proc = applied({**FULL_ARGS, "Mission": ""})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "mission: 'St. Mary\\'s Shelter is a nonprofit organization.'," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_candid_urls_are_never_derived_from_the_ein():
    # The footer seal's alt text claims a Candid "Platinum" level, so only a
    # URL the charity provides is ever linked: not a profile-by-EIN URL (not
    # even for a recognized 501(c)(3)), and never FFC's own profile.
    for irs in ("501(c)(3) (approved)", "Not yet / pending (pre-501(c)(3))", ""):
        td = pathlib.Path(tempfile.mkdtemp())
        try:
            repo = make_repo(td)
            summary = td / "summary.json"
            proc = run_apply(repo, {**FULL_ARGS, "IrsStatus": irs, "SummaryPath": str(summary)})
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")
            assert "guidestar: {\n    profileUrl: '',\n    directProfileUrl: '',\n  }," in cfg, (irs, cfg)
            assert "guidestar.org" not in cfg, (irs, cfg)
            pending = json.loads(summary.read_text(encoding="utf-8"))["pendingFields"]
            assert "guidestar" in pending, (irs, pending)
        finally:
            shutil.rmtree(td)


def test_provided_candid_urls_are_used_as_given():
    args = {
        **FULL_ARGS,
        "GuideStarProfileUrl": "https://www.guidestar.org/profile/12-3456789",
        "GuideStarDirectProfileUrl": "https://www.guidestar.org/profile/shared/abc",
    }
    td, repo, proc = applied(args)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "profileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
        assert "directProfileUrl: 'https://www.guidestar.org/profile/shared/abc'," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_tel_is_the_published_numbers_digits_with_no_invented_country_code():
    cases = {
        "(555) 123-4567": "5551234567",
        "555.123.4567": "5551234567",
        "1-555-123-4567": "15551234567",
        "+1 555 123 4567": "+15551234567",
        "  +1 (555) 123-4567 ": "+15551234567",
    }
    for published, tel in cases.items():
        td, repo, proc = applied({**FULL_ARGS, "FooterPhone": published})
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")
            want = f"phone: {{ display: '{published.strip()}', tel: '{tel}' }},"
            assert want in cfg, (published, cfg)
        finally:
            shutil.rmtree(td)
    # Not a usable US number: emptied and pending, never FFC's.
    td, repo, proc = applied({**FULL_ARGS, "FooterPhone": "12345"})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "phone: { display: '', tel: '' }," in read(repo, "src/lib/site.config.ts")
    finally:
        shutil.rmtree(td)


def test_a_template_requiring_sealUrl_keeps_the_key_and_gets_it_empty():
    # The Footer-Only template declares `guidestar: { sealUrl; profileUrl;
    # directProfileUrl }`, all REQUIRED. Replacing the object without sealUrl
    # produced TS2741 and `next build` failed, so the provisioned site never
    # deployed -- all three sample charities, 748 run 37164489698. The seal is
    # keyed to an organization's own Candid id, so it cannot be derived from an
    # EIN; FFC's own widget URL would be a transparency claim about FFC. It is
    # therefore written EMPTY, and the template renders the seal only when set.
    footer_only_shape = SITE_CONFIG.replace(
        "  guidestar: {\n"
        "    profileUrl: 'https://www.guidestar.org/profile/46-2471893',\n"
        "    directProfileUrl: 'https://www.guidestar.org/profile/shared/bbbe173a',\n"
        "  },",
        "  guidestar: {\n"
        "    sealUrl: 'https://widgets.guidestar.org/prod/v1/pdp/"
        "transparency-seal/9326392/svg',\n"
        "    profileUrl: 'https://www.guidestar.org/profile/46-2471893',\n"
        "    directProfileUrl: 'https://www.guidestar.org/profile/shared/bbbe173a',\n"
        "  },",
    )
    # Guard the substitution itself: a fixture reshuffle must fail loudly here
    # rather than silently testing the shape this test exists to cover.
    assert "sealUrl:" in footer_only_shape, "fixture substitution did not apply"

    # The Candid URL is passed explicitly rather than left to FULL_ARGS. This
    # branch deliberately stopped DERIVING a profile URL from the EIN, so
    # FULL_ARGS -- an EIN and no URL -- now takes the empty/pending path, and
    # asserting a derived URL here would assert the behaviour this PR removes.
    args = {
        **FULL_ARGS,
        "GuideStarProfileUrl": "https://www.guidestar.org/profile/12-3456789",
    }
    td, repo, proc = applied(args, site_config=footer_only_shape)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "sealUrl: ''," in cfg, cfg
        # FFC's own Candid organization id must not survive under the charity.
        assert "9326392" not in cfg, cfg
        # The other two keys still get the charity's own profile.
        assert "profileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
        assert (
            "directProfileUrl: 'https://www.guidestar.org/profile/12-3456789',"
            in cfg
        ), cfg
    finally:
        shutil.rmtree(td)

    # The pending path must carry sealUrl too. This is the combination neither
    # parent produced: main wrote guidestar only when a profile URL was present
    # (so a required sealUrl was never emitted on the empty path), and this
    # branch writes it always but had no sealUrl at all. Without this case the
    # TS2741 break survives for exactly the charity #1431 exists to serve -- one
    # with no Candid profile yet.
    td, repo, proc = applied(site_config=footer_only_shape)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "sealUrl: ''," in cfg, cfg
        assert "\n    profileUrl: '',"  in cfg, cfg
        assert "\n    directProfileUrl: '',"  in cfg, cfg
        assert "9326392" not in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_address_line_breaks_real_or_literal_but_never_commas():
    cases = {
        "12 Main St\nSpringfield, IL 62701": ["12 Main St", "Springfield, IL 62701"],
        "12 Main St\r\nSpringfield, IL 62701": ["12 Main St", "Springfield, IL 62701"],
        # What bash passes for '12 Main St\nSpringfield, IL 62701'.
        "12 Main St\\nSpringfield, IL 62701": ["12 Main St", "Springfield, IL 62701"],
        "12 Main St\\r\\nSpringfield, IL 62701": ["12 Main St", "Springfield, IL 62701"],
        "Suite 2\\n 7 Rue des Artistes \\n\\nPortland, ME 04101": ["Suite 2", "7 Rue des Artistes", "Portland, ME 04101"],
        "St. Petersburg, FL": ["St. Petersburg, FL"],
    }
    for given, lines in cases.items():
        td, repo, proc = applied({**FULL_ARGS, "FooterAddress": given})
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")
            want = "lines: [" + ", ".join("'" + ln + "'" for ln in lines) + "],"
            assert want in cfg, (given, cfg)
            assert "\\n" not in cfg.split("addresses:")[1].split("]")[0], (given, cfg)
        finally:
            shutil.rmtree(td)


def test_a_template_without_sealUrl_does_not_gain_one():
    # The Single Page template has no sealUrl. Writing one would be an excess
    # property against its SiteConfig and fail `tsc` there -- the same break in
    # the opposite direction, so the key set is read from the template, never
    # assumed.
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "sealUrl" not in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_keeps_ffc_attribution_and_drops_the_parent_org():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "supportedBy: {\n    name: 'Free For Charity'," in cfg, cfg
        assert "parentOrg:" not in cfg.split("export const siteConfig")[1], cfg
        # Untouched keys, comments and code after the literal survive.
        assert "keywords: ['nonprofit', 'charity', 'donate', 'volunteer'," in cfg, cfg
        assert "// Empty = the footer's Donate" in cfg, cfg
        assert "export function sitePath(path = '/'): string {" in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_team_data_uses_the_role_schema_and_keeps_other_exports():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        team_dir = repo / "src" / "data" / "team"
        files = sorted(p.name for p in team_dir.glob("*.json"))
        assert files == ["jane-o-doe.json", "jim-roe.json"], files
        jane = json.loads(read(repo, "src/data/team/jane-o-doe.json"))
        assert jane == {"name": "Jane O'Doe", "role": "President"}, jane
        jim = json.loads(read(repo, "src/data/team/jim-roe.json"))
        assert jim == {
            "name": "Jim Roe",
            "role": "Treasurer",
            "linkedinUrl": "https://www.linkedin.com/in/jimroe",
        }, jim
        team_ts = read(repo, "src/data/team.ts")
        assert "import member1 from './team/jane-o-doe.json'" in team_ts, team_ts
        assert "clarke-moyer" not in team_ts, team_ts
        assert "export const team: TeamMember[] = [\n  member1,\n  member2,\n]" in team_ts, team_ts
        assert "export type TeamMember = {" in team_ts, team_ts
        assert "export const configuredTeam" in team_ts, team_ts
    finally:
        shutil.rmtree(td)


def test_no_usable_leadership_leaves_an_empty_team_not_ffcs():
    # 701 counts raw lines, so lines that parse to no name still reach the
    # script. Keeping the template's sample team would publish FFC's people as
    # the charity's leadership; an empty team is the honest state (both
    # templates' team sections render nothing for it).
    td, repo, proc = applied({**FULL_ARGS, "LeadershipLines": ["| Treasurer", "|"]})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "team is left empty" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
        assert list((repo / "src" / "data" / "team").glob("*.json")) == []
        team_ts = read(repo, "src/data/team.ts")
        assert "export const team: TeamMember[] = []" in team_ts, team_ts
        assert "import " not in team_ts and "clarke" not in team_ts.lower(), team_ts
        # The rest of team.ts (the type, derived exports) is kept.
        assert "export type TeamMember = {" in team_ts, team_ts
        assert "export const configuredTeam" in team_ts, team_ts
        assert "\n\n\n" not in team_ts, team_ts
    finally:
        shutil.rmtree(td)


def test_a_team_left_empty_can_be_filled_by_a_later_run():
    # Re-running once the charity supplies its leadership must still work on a
    # team.ts that has no ./team/*.json imports left.
    td, repo, proc = applied({**FULL_ARGS, "LeadershipLines": []})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        again = run_apply(repo, FULL_ARGS)
        assert again.returncode == 0, again.stdout + again.stderr
        team_ts = read(repo, "src/data/team.ts")
        assert "import member1 from './team/jane-o-doe.json'" in team_ts, team_ts
        assert "export const team: TeamMember[] = [\n  member1,\n  member2,\n]" in team_ts, team_ts
        # The imports sit above the first export, as the template has them.
        assert team_ts.index("import member1") < team_ts.index("export type TeamMember"), team_ts
    finally:
        shutil.rmtree(td)


def test_a_blank_ein_is_written_blank_not_ffcs():
    td, repo, proc = applied({**FULL_ARGS, "FooterEin": "  "})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "ein: ''," in cfg, cfg
        assert "46-2471893" not in cfg, cfg
        assert "No EIN supplied" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(td)


def test_a_direct_candid_link_alone_fills_both_urls():
    args = {**FULL_ARGS, "GuideStarDirectProfileUrl": "https://www.guidestar.org/profile/shared/abc"}
    td, repo, proc = applied({**args, "IrsStatus": ""})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "profileUrl: 'https://www.guidestar.org/profile/shared/abc'," in cfg, cfg
        assert "directProfileUrl: 'https://www.guidestar.org/profile/shared/abc'," in cfg, cfg
    finally:
        shutil.rmtree(td)


# Mirrors FFC-EX-iwilf.org's application (FFC-EX-iwilf.org#6): a pre-501(c)(3)
# charity with a name, email, EIN, mission, a single LinkedIn link and three
# leaders, and NO phone, address, Candid profile or other social links. 701
# used to skip the whole patch for it, and the site went live as FFC.
SPARSE_ARGS = {
    "Domain": "iwilf.example",
    "CharityName": "Interpreters Legacy Test Foundation",
    "FooterEmail": "legacy@iwilf.example",
    "FooterPhone": "",
    "FooterAddress": "",
    "FooterEin": "42-0000124",
    "GuideStarProfileUrl": "",
    "GuideStarDirectProfileUrl": "",
    "FooterSocial": ["LinkedIn: https://www.linkedin.com/company/iwilf-test/"],
    "LeadershipLines": [
        "Founder - Ali Example",
        "Samer Example | Treasurer",
        "Volunteer Coordinator - Adnan Example",
    ],
    "Mission": "Preserving and honoring the legacy of the interpreters who served alongside U.S. forces.",
    "DonationUrl": "",
    "VolunteerUrl": "",
    "IrsStatus": "Not yet / pending (pre-501(c)(3))",
}

# Free For Charity's own identity as the templates ship it. None of it may
# survive on a charity's site, outside the permanent "Supported by" attribution.
FFC_IDENTITY = (
    "46-2471893",
    "(520) 222-8104",
    "5202228104",
    "Raleigh",
    "Wake Forrest",
    "State College",
    "Science Park",
    "facebook.com/freeforcharity",
    "x.com/freeforcharity1",
    "@freeforcharity",
    "linkedin.com/company/freeforcharity",
    "github.com/FreeForCharity",
    "guidestar.org",
    "bbbe173a",
    "clarkemoyer@",
    "Reduce Costs",
    "Clarke Moyer",
    "Chris Rae",
    "clarke-moyer",
    "chris-rae",
)


# The pending convention (FFC-EX-iwilf.org#6; template issues
# FFC-IN-Footer_Only_Template#169 / FFC-IN-FFC_Single_Page_Template#482): a
# template whose SiteConfig declares `pending` renders each listed field as a
# visible "Awaiting information from the charity" placeholder.
SITE_CONFIG_WITH_PENDING = SITE_CONFIG.replace(
    "export type SiteConfig = {\n  name: string\n",
    "export type PendingField =\n  | 'email'\n  | 'phone'\n  | 'address'\n  | 'ein'\n  | 'guidestar'\n"
    "  | 'social'\n  | 'team'\n  | 'donationUrl'\n  | 'volunteerUrl'\n\n"
    "export const PENDING_TEXT = 'Awaiting information from the charity'\n\n"
    "export type SiteConfig = {\n  name: string\n  pending?: readonly PendingField[]\n",
)
assert SITE_CONFIG_WITH_PENDING != SITE_CONFIG

# Every footer-standard field provided, so nothing is pending.
COMPLETE_ARGS = {
    **FULL_ARGS,
    "VolunteerUrl": "https://helpinghands.example/volunteer",
    "GuideStarProfileUrl": "https://www.guidestar.org/profile/12-3456789",
}

# iwilf's missing fields, in the order the script checks them.
SPARSE_PENDING = ["donationUrl", "volunteerUrl", "phone", "address", "guidestar"]


def apply_with_summary(args: dict, site_config: str = SITE_CONFIG):
    td = pathlib.Path(tempfile.mkdtemp())
    repo = make_repo(td, site_config)
    summary = td / "summary.json"
    proc = run_apply(repo, {**args, "SummaryPath": str(summary)})
    data = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None
    return td, repo, proc, data


def assert_no_ffc_identity(repo: pathlib.Path):
    cfg = read(repo, "src/lib/site.config.ts")
    config_body = cfg.split("export const siteConfig")[1].split("supportedBy:")[0]
    for ffc in FFC_IDENTITY:
        assert ffc not in config_body, (ffc, cfg)
    # FFC attribution stays, and is the only FFC reference.
    assert "supportedBy: {\n    name: 'Free For Charity'," in cfg, cfg
    team_files = [read(repo, "src/data/team.ts")] + [
        p.read_text(encoding="utf-8") for p in (repo / "src" / "data" / "team").glob("*.json")
    ]
    for text in team_files:
        for ffc in FFC_IDENTITY:
            assert ffc not in text, (ffc, text)


def test_a_sparse_charity_gets_its_own_details_and_none_of_ffcs():
    for site_config, rendered in ((SITE_CONFIG, False), (SITE_CONFIG_WITH_PENDING, True)):
        td, repo, proc, summary = apply_with_summary(SPARSE_ARGS, site_config)
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")

            # What the charity gave is on the site.
            assert "name: 'Interpreters Legacy Test Foundation'," in cfg, cfg
            assert "contactEmail: 'legacy@iwilf.example'," in cfg, cfg
            assert "ein: '42-0000124'," in cfg, cfg
            assert "Preserving and honoring the legacy" in cfg, cfg
            assert (
                "social: [\n    { label: 'LinkedIn', href: 'https://www.linkedin.com/company/iwilf-test/' },\n  ],"
                in cfg
            ), cfg

            # What it did not give is emptied, never FFC's.
            assert "phone: { display: '', tel: '' }," in cfg, cfg
            assert "addresses: []," in cfg, cfg
            assert "guidestar: {\n    profileUrl: '',\n    directProfileUrl: '',\n  }," in cfg, cfg
            assert "twitterHandle: ''," in cfg, cfg
            assert "taxStatusLabel: ''," in cfg, cfg  # a legal claim, never pending
            assert "donationUrl: ''," in cfg and "volunteerUrl: ''," in cfg, cfg
            assert_no_ffc_identity(repo)

            # The team is the charity's three leaders, none of FFC's staff.
            members = sorted(
                json.loads(p.read_text(encoding="utf-8"))["name"]
                for p in (repo / "src" / "data" / "team").glob("*.json")
            )
            assert members == ["Adnan Example", "Ali Example", "Samer Example"], members

            for rel in ("public/security.txt", "public/.well-known/security.txt"):
                body = read(repo, rel)
                assert body.startswith("Contact: mailto:legacy@iwilf.example\n"), body

            # Pending is exactly the missing fields, reported for 701 either way...
            assert summary == {"pendingFields": SPARSE_PENDING, "pendingRendered": rendered}, summary
            # ...and written into siteConfig only when the template renders it.
            body = cfg.split("export const siteConfig")[1]
            if rendered:
                assert (
                    "  pending: ['donationUrl', 'volunteerUrl', 'phone', 'address', 'guidestar'],\n}"
                    in body
                ), cfg
            else:
                assert "pending" not in body, cfg
                assert "has no 'pending' key yet" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
        finally:
            shutil.rmtree(td)


def test_a_complete_charity_has_nothing_pending():
    args = COMPLETE_ARGS
    td, repo, proc, summary = apply_with_summary(args, SITE_CONFIG_WITH_PENDING)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert summary == {"pendingFields": [], "pendingRendered": True}, summary
        assert "pending" not in read(repo, "src/lib/site.config.ts").split("export const siteConfig")[1]
    finally:
        shutil.rmtree(td)


def test_every_missing_field_is_emptied_and_pending():
    args = {
        **SPARSE_ARGS,
        "FooterEmail": "",
        "FooterEin": "",
        "FooterSocial": [],
        "LeadershipLines": [],
        "Mission": "",
    }
    td, repo, proc, summary = apply_with_summary(args, SITE_CONFIG_WITH_PENDING)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "contactEmail: ''," in cfg and "social: []," in cfg and "ein: ''," in cfg, cfg
        assert_no_ffc_identity(repo)
        assert sorted(summary["pendingFields"]) == sorted(
            ["email", "donationUrl", "volunteerUrl", "ein", "phone", "address", "guidestar", "social", "team"]
        ), summary
        # The team is written after siteConfig; `pending` still names it.
        assert "'team']," in cfg, cfg
        # No charity email: security.txt keeps the template's reachable
        # address rather than an empty Contact line.
        assert read(repo, "public/security.txt").startswith("Contact: mailto:clarkemoyer@"), cfg
    finally:
        shutil.rmtree(td)


def test_pending_is_updated_and_then_removed_by_later_runs():
    td, repo, proc, _ = apply_with_summary(SPARSE_ARGS, SITE_CONFIG_WITH_PENDING)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # The charity sends its phone: the list shrinks in place.
        again = run_apply(repo, {**SPARSE_ARGS, "FooterPhone": "(555) 010-0999"})
        assert again.returncode == 0, again.stdout + again.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert cfg.count("pending:") == 1, cfg
        assert "pending: ['donationUrl', 'volunteerUrl', 'address', 'guidestar']," in cfg, cfg
        # Everything arrives: the key and its comment go.
        done = run_apply(repo, COMPLETE_ARGS)
        assert done.returncode == 0, done.stdout + done.stderr
        body = read(repo, "src/lib/site.config.ts").split("export const siteConfig")[1]
        assert "pending" not in body, body
        assert "awaiting information" not in body, body
    finally:
        shutil.rmtree(td)


def test_pending_support_is_read_from_the_type_not_a_nested_key():
    # A `pending` member of some nested object type is not SiteConfig.pending.
    nested = SITE_CONFIG.replace(
        "  parentOrg?: { name: string; url: string; hubUrl: string }\n",
        "  parentOrg?: { name: string; url: string; hubUrl: string; pending?: boolean }\n",
    )
    assert nested != SITE_CONFIG
    td, repo, proc, summary = apply_with_summary(SPARSE_ARGS, nested)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert summary["pendingRendered"] is False, summary
    finally:
        shutil.rmtree(td)


def test_the_legacy_footer_path_refuses_to_keep_ffcs_values():
    # Pre-site.config.ts repos: the regex patch can only replace, not blank,
    # so a sparse charity must fail (content_status=failed) rather than ship
    # FFC's phone / address / social links under the charity's name.
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = td / "repo"
        (repo / "src" / "components" / "footer").mkdir(parents=True)
        footer = repo / "src" / "components" / "footer" / "index.tsx"
        footer.write_text('<a href="tel:15202228104">(520) 222-8104</a>\n', encoding="utf-8")
        proc = run_apply(repo, SPARSE_ARGS)
        assert proc.returncode != 0, proc.stdout
        out = proc.stdout + proc.stderr
        assert "Legacy hard-coded footer" in out and "phone" in out and "address" in out, out
        assert "(520) 222-8104" in footer.read_text(encoding="utf-8")  # untouched, not half-patched
    finally:
        shutil.rmtree(td)


INTEGRATIONS = """  sections: {
    showEndowment: true,
    showPrograms: true,
  },
  integrations: {
    zeffyDonationUrl: 'https://www.zeffy.com/embed/donation-form/free-for-charity-endowment-fund',
    idealistUrl:
      'https://www.idealist.org/en/nonprofit/356bfc8e2ae64f83beea4a4e677e99d7-free-for-charity-state-college#opportunities',
    eventsFacebookPageUrl: 'https://www.facebook.com/freeforcharity',
    microsoftFormUrl: 'https://forms.office.com/r/vePxGq6JqG',
  },
}
"""
# An older Single Page shape (FFC-EX-vcof.org): integrations with no guard.
SITE_CONFIG_UNGUARDED_INTEGRATIONS = SITE_CONFIG.replace(
    "    hubUrl: 'https://freeforcharity.org/hub/',\n  },\n}\n",
    "    hubUrl: 'https://freeforcharity.org/hub/',\n  },\n" + INTEGRATIONS,
)
assert SITE_CONFIG_UNGUARDED_INTEGRATIONS != SITE_CONFIG
# The current shape: the same keys, used only on FFC's own site.
SITE_CONFIG_GUARDED_INTEGRATIONS = SITE_CONFIG_UNGUARDED_INTEGRATIONS + (
    "\nexport function isSupportingOrgSite(): boolean {\n"
    "  return siteConfig.name.trim() === siteConfig.supportedBy.name.trim()\n}\n"
)


def test_unguarded_integrations_are_emptied_and_the_ffc_endowment_hidden():
    # A pending donation / volunteer URL must never leave FFC's Zeffy and
    # Idealist pages behind the charity's buttons on an older template.
    td, repo, proc = applied(SPARSE_ARGS, SITE_CONFIG_UNGUARDED_INTEGRATIONS)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        body = cfg.split("export const siteConfig")[1]
        for ffc in ("zeffy.com", "idealist.org", "facebook.com/freeforcharity", "forms.office.com"):
            assert ffc not in body, (ffc, cfg)
        assert "zeffyDonationUrl: ''," in body and "microsoftFormUrl: ''," in body, cfg
        assert "idealistUrl: ''," in body or "idealistUrl:\n      ''," in body, cfg
        assert "showEndowment: false," in body and "showPrograms: true," in body, cfg
    finally:
        shutil.rmtree(td)


def test_guarded_integrations_are_left_for_the_supporting_org_site():
    # Current Single Page: rendered only when isSupportingOrgSite(), and the
    # template's own tests read these values, so they stay as shipped.
    td, repo, proc = applied(SPARSE_ARGS, SITE_CONFIG_GUARDED_INTEGRATIONS)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        body = read(repo, "src/lib/site.config.ts").split("export const siteConfig")[1]
        assert "free-for-charity-endowment-fund" in body and "showEndowment: true," in body, body
    finally:
        shutil.rmtree(td)


LEGACY_FOOTER = """<a href="mailto:clarkemoyer@freeforcharity.org">clarkemoyer@freeforcharity.org</a>
<a href="tel:15202228104">(520) 222-8104</a>
const socials = [
  { href: 'https://www.facebook.com/freeforcharity' },
  { href: 'https://x.com/freeforcharity1' },
  { href: 'https://www.linkedin.com/company/freeforcharity/' },
  { href: 'https://github.com/FreeForCharity' },
]
"""


def make_legacy_repo(td: pathlib.Path) -> pathlib.Path:
    repo = td / "repo"
    (repo / "src" / "components" / "footer").mkdir(parents=True)
    (repo / "src" / "components" / "home-page" / "TheFreeForCharityTeam").mkdir(parents=True)
    (repo / "src" / "data" / "team").mkdir(parents=True)
    (repo / "src" / "components" / "footer" / "index.tsx").write_text(LEGACY_FOOTER, encoding="utf-8")
    (repo / "src" / "components" / "home-page" / "TheFreeForCharityTeam" / "index.tsx").write_text(
        "export default null\n", encoding="utf-8"
    )
    (repo / "src" / "data" / "team.ts").write_text("export const team = []\n", encoding="utf-8")
    return repo


def test_the_legacy_path_accepts_the_same_social_labels_as_the_config_path():
    # "X (Twitter)" is a label Get-SocialEntries accepts; the legacy guard used
    # a stricter parse and refused it, and the legacy patch would not have
    # replaced FFC's X link with it.
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_legacy_repo(td)
        args = {
            **FULL_ARGS,
            "FooterSocial": [
                "Facebook: https://www.facebook.com/helpinghands",
                "X (Twitter): https://x.com/helpinghands",
                "LinkedIn: https://www.linkedin.com/company/helpinghands",
                "GitHub: https://github.com/helpinghands",
            ],
        }
        proc = run_apply(repo, args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        footer = read(repo, "src/components/footer/index.tsx")
        assert "href: 'https://x.com/helpinghands'" in footer, footer
        for ffc in ("x.com/freeforcharity1", "facebook.com/freeforcharity'", "company/freeforcharity/"):
            assert ffc not in footer, (ffc, footer)
    finally:
        shutil.rmtree(td)


def test_security_txt_contact_follows_the_contact_email():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for rel in ("public/security.txt", "public/.well-known/security.txt"):
            body = read(repo, rel)
            assert body.startswith("Contact: mailto:info@helpinghands.org\n"), body
            assert "Expires: 2027-01-01" in body, body
    finally:
        shutil.rmtree(td)


def test_written_files_are_lf_only():
    # The content job runs on windows-latest; the templates enforce LF.
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for rel in ("src/lib/site.config.ts", "src/data/team.ts", "public/security.txt"):
            assert b"\r" not in (repo / rel).read_bytes(), rel
    finally:
        shutil.rmtree(td)


def test_a_template_shape_change_fails_loudly():
    # A missing required key must throw (content_status=failed) rather than
    # silently ship FFC's value on the charity's site.
    td, repo, proc = applied(site_config=SITE_CONFIG.replace("  ein: '46-2471893',\n", ""))
    try:
        assert proc.returncode != 0, proc.stdout
        assert "siteConfig has no 'ein' key" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(td)


def test_an_issue_form_address_with_a_literal_backslash_n_is_split():
    # 701's issue-form path used to emit "line1\\nline2" (a backslash and an n).
    td, repo, proc = applied({**FULL_ARGS, "FooterAddress": "12 Main St\\nSpringfield, IL 62701"})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "lines: ['12 Main St', 'Springfield, IL 62701']," in cfg, cfg
        assert "%5Cn" not in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_a_dollar_sign_in_the_email_is_written_literally_to_security_txt():
    td, repo, proc = applied({**FULL_ARGS, "FooterEmail": "don$&ate@helpinghands.org"})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        body = read(repo, "public/security.txt")
        assert body.startswith("Contact: mailto:don$&ate@helpinghands.org\n"), body
    finally:
        shutil.rmtree(td)


def test_one_person_in_two_offices_gets_one_card_with_both_roles():
    lines = ["Jane Doe | President", "Jane Doe | Secretary", "Jim Roe | Treasurer"]
    td, repo, proc = applied({**FULL_ARGS, "LeadershipLines": lines})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        files = sorted(p.name for p in (repo / "src" / "data" / "team").glob("*.json"))
        assert files == ["jane-doe.json", "jim-roe.json"], files
        jane = json.loads(read(repo, "src/data/team/jane-doe.json"))
        assert jane["role"] == "President & Secretary", jane
    finally:
        shutil.rmtree(td)


def test_a_short_mission_still_yields_a_long_enough_description():
    # The templates' own metadata test requires a description over 50 chars.
    for mission in ("We shelter families.", ""):
        td, repo, proc = applied({**FULL_ARGS, "Mission": mission})
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")
            import re

            desc = re.search(r"description:\s*'((?:[^'\\]|\\.)*)'", cfg).group(1)
            assert len(desc) > 50, (mission, desc)
            assert "St. Mary" in desc, desc
        finally:
            shutil.rmtree(td)


def test_ffc_tagline_keywords_and_founding_date_do_not_survive():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "tagline: 'Nonprofit Organization'," in cfg, cfg
        assert "keywords: ['nonprofit', 'charity', 'donate', 'volunteer', 'St. Mary\\'s Shelter']," in cfg, cfg
        assert "foundingDate" not in cfg.split("export const siteConfig")[1], cfg
    finally:
        shutil.rmtree(td)


def test_tax_status_claims_follow_the_irs_status():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "taxStatusLabel: 'a US 501c3 Non Profit'," in cfg, cfg
        assert "nonprofitStatus: 'https://schema.org/Nonprofit501c3'," in cfg, cfg
    finally:
        shutil.rmtree(td)
    for pending in ("Not yet / pending (pre-501(c)(3))", ""):
        td, repo, proc = applied({**FULL_ARGS, "IrsStatus": pending})
        try:
            assert proc.returncode == 0, proc.stdout + proc.stderr
            cfg = read(repo, "src/lib/site.config.ts")
            assert "taxStatusLabel: ''," in cfg, (pending, cfg)
            assert "nonprofitStatus" not in cfg.split("export const siteConfig")[1], (pending, cfg)
        finally:
            shutil.rmtree(td)


def test_a_second_run_over_the_same_repo_succeeds():
    # After prettier a short team array sits on one line; the rewrite must
    # still find it, or re-provisioning an existing repo throws.
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        team_ts = repo / "src" / "data" / "team.ts"
        text = read(repo, "src/data/team.ts")
        one_line = text.replace("[\n  member1,\n  member2,\n]", "[member1, member2]")
        assert one_line != text, text
        team_ts.write_bytes(one_line.encode("utf-8"))
        again = run_apply(repo, FULL_ARGS)
        assert again.returncode == 0, again.stdout + again.stderr
        assert "export const team: TeamMember[] = [\n  member1,\n  member2,\n]" in read(
            repo, "src/data/team.ts"
        )
    finally:
        shutil.rmtree(td)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
