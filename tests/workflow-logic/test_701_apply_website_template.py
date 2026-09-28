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
        assert "phone: { display: '(555) 123-4567', tel: '15551234567' }," in cfg, cfg
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


def test_blank_candid_urls_derive_from_the_ein_for_a_recognized_501c3_not_ffc():
    # Candid carries every IRS-recognized exempt org, so for a recognized
    # 501(c)(3) the profile-by-EIN URL is the charity's own profile. FFC's own
    # profile must never be left on the charity's footer. (Not recognized:
    # blank, see test_a_pre_501c3_charity_without_candid_urls_gets_blank_guidestar.)
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "profileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
        assert "directProfileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
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


def test_a_pre_501c3_charity_without_candid_urls_gets_blank_guidestar():
    # A charity without IRS recognition has no Candid profile: a URL derived
    # from its EIN would be a dead link behind a transparency seal, and FFC's
    # own profile would be a false claim. Both are left blank.
    args = {**FULL_ARGS, "IrsStatus": "Not yet / pending (pre-501(c)(3))"}
    td, repo, proc = applied(args)
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "guidestar: {\n    profileUrl: '',\n    directProfileUrl: '',\n  }," in cfg, cfg
        assert "guidestar.org" not in cfg, cfg
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


def test_a_sparse_charity_gets_its_own_details_and_none_of_ffcs():
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td)
        summary = td / "summary.json"
        proc = run_apply(repo, {**SPARSE_ARGS, "SummaryPath": str(summary)})
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

        # What it did not give is blank, never FFC's.
        assert "phone: { display: '', tel: '' }," in cfg, cfg
        assert "addresses: []," in cfg, cfg
        assert "guidestar: {\n    profileUrl: '',\n    directProfileUrl: '',\n  }," in cfg, cfg
        assert "twitterHandle: ''," in cfg, cfg
        assert "taxStatusLabel: ''," in cfg, cfg
        assert "donationUrl: ''," in cfg and "volunteerUrl: ''," in cfg, cfg
        config_body = cfg.split("export const siteConfig")[1].split("supportedBy:")[0]
        for ffc in FFC_IDENTITY:
            assert ffc not in config_body, (ffc, cfg)
        # FFC attribution stays, and is the only FFC reference.
        assert "supportedBy: {\n    name: 'Free For Charity'," in cfg, cfg

        # The team is the charity's three leaders, none of FFC's staff.
        members = sorted(
            json.loads(p.read_text(encoding="utf-8"))["name"]
            for p in (repo / "src" / "data" / "team").glob("*.json")
        )
        assert members == ["Adnan Example", "Ali Example", "Samer Example"], members
        team_files = [read(repo, "src/data/team.ts")] + [
            p.read_text(encoding="utf-8") for p in (repo / "src" / "data" / "team").glob("*.json")
        ]
        for text in team_files:
            for ffc in FFC_IDENTITY:
                assert ffc not in text, (ffc, text)

        for rel in ("public/security.txt", "public/.well-known/security.txt"):
            body = read(repo, rel)
            assert body.startswith("Contact: mailto:legacy@iwilf.example\n"), body

        # The blanks are reported for 701's completion comment.
        reported = json.loads(summary.read_text(encoding="utf-8"))["blankFields"]
        assert reported == ["phone", "address", "Candid/GuideStar profile"], reported
    finally:
        shutil.rmtree(td)


def test_a_complete_charity_reports_no_blank_fields():
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td)
        summary = td / "summary.json"
        proc = run_apply(repo, {**FULL_ARGS, "SummaryPath": str(summary)})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert json.loads(summary.read_text(encoding="utf-8"))["blankFields"] == []
    finally:
        shutil.rmtree(td)


def test_every_missing_field_is_blank_and_reported():
    args = {
        **SPARSE_ARGS,
        "FooterEin": "",
        "FooterSocial": [],
        "LeadershipLines": [],
        "Mission": "",
    }
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td)
        summary = td / "summary.json"
        proc = run_apply(repo, {**args, "SummaryPath": str(summary)})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "social: []," in cfg and "ein: ''," in cfg, cfg
        config_body = cfg.split("export const siteConfig")[1].split("supportedBy:")[0]
        for ffc in FFC_IDENTITY:
            assert ffc not in config_body, (ffc, cfg)
        reported = json.loads(summary.read_text(encoding="utf-8"))["blankFields"]
        assert sorted(reported) == sorted(
            ["mission", "EIN", "phone", "address", "Candid/GuideStar profile", "social links", "leadership"]
        ), reported
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
