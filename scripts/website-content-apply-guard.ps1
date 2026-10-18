<#
.SYNOPSIS
    Input guard for workflow 707 (Website - Content Apply): decides whether a
    content payload may be written into an FFC-EX repo.

.DESCRIPTION
    Workflow 707 lets a cloud worker agent deliver a charity's site content into
    its `FFC-EX-<domain>` repo as a pull request. The worker cannot reach that
    repo itself (cloud sessions only see repos attached when they start), so the
    hub is the only writer, and this guard is what bounds the write.

    Everything here is offline and pure: no network, no git, no GitHub API. The
    workflow's ungated `validate` job and its gated `apply` job both run the SAME
    function (Invoke-ContentApplyValidation), so the files `apply` writes are
    exactly the files `validate` printed as the plan.

    What it refuses, in order of how much damage the input could do:
      * a target repo that is not `FFC-EX-<lowercase name>` (the org-wide PAT
        could otherwise write any repo in FreeForCharity);
      * any path outside the allowlist in config/website-content-apply-paths.json;
      * HARD refusals that no config edit can relax: `.github/`, workflow files,
        CNAME, package manifests and lockfiles, `..`, absolute paths, backslashes,
        characters outside [A-Za-z0-9._/-], extensions outside the configured set;
      * a payload over the configured byte budget (60 KB), duplicate or
        case-colliding paths, non-string values, NUL bytes, invalid JSON in a
        `.json` file, and code-execution constructs in a `.ts` file.

    Dot-source it to load the functions (the Pester suite does); it returns
    before doing anything else when dot-sourced.

    Pester: scripts/tests/website-content-apply-guard.Tests.ps1
#>
[CmdletBinding()]
param()

# Repo names: the FFC-EX fleet, lowercase. Mixed-case legacy repos
# (e.g. FFC-EX-SRRN.net) are refused on purpose -- this workflow is for sites
# 701 provisions, which are named from the lowercased domain.
$script:ContentApplyRepoPattern = '^FFC-EX-[a-z0-9.-]+$'

# Characters a path may contain at all. Everything the templates' content files
# use fits, and it rules out whitespace, quoting, shell metacharacters, Unicode
# look-alikes and URL-encoding tricks before any other test runs.
$script:ContentApplyPathCharPattern = '^[A-Za-z0-9._/-]+$'

# Basenames refused anywhere in the tree, whatever the allowlist says.
$script:ContentApplyDeniedBasenames = @(
    'CNAME',
    'package.json',
    'package-lock.json',
    'npm-shrinkwrap.json',
    'pnpm-lock.yaml',
    'pnpm-workspace.yaml',
    'yarn.lock',
    'bun.lockb',
    'bun.lock',
    '.npmrc',
    '.nvmrc',
    '.gitattributes',
    '.gitignore',
    '.gitmodules'
)

# Top-level directories refused whatever the allowlist says.
$script:ContentApplyDeniedRoots = @('.github', '.git', '.husky', '.claude', '.vscode', 'node_modules', 'public')

# Constructs refused in a `.ts` content file. Content modules are data: object
# literals and imports of sibling data files. These are the ways a `.ts` file
# turns into code that runs at build time with the site CI's token in reach.
$script:ContentApplyTsDenied = [ordered]@{
    'dynamic import()'                       = '\bimport\s*\('
    'require()'                              = '\brequire\s*\('
    'eval()'                                 = '\beval\s*\('
    'new Function()'                         = '\bnew\s+Function\b'
    # The templates' own site.config.ts reads process.env.NEXT_PUBLIC_BASE_PATH,
    # and NEXT_PUBLIC_* values are public by construction; anything else is not.
    'process.env (other than NEXT_PUBLIC_*)' = '\bprocess\s*\.\s*env\b(?!\s*\.\s*NEXT_PUBLIC_[A-Z0-9_]+\b)'
    'child_process'                          = 'child_process'
    'a <script> tag'                         = '<\s*script\b'
    'dangerouslySetInnerHTML'                = 'dangerouslySetInnerHTML'
    'a Node built-in module (node:*)'        = '[''"]node:'
}

function ConvertTo-ContentApplyGlobRegex {
    <#
    .SYNOPSIS
        Converts an allowlist glob to an anchored regex.
    .DESCRIPTION
        `**/` matches zero or more whole path segments, `*` matches within one
        segment (never `/`), `?` matches one non-`/` character. Everything else
        is literal.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$Glob)

    $sb = [System.Text.StringBuilder]::new('^')
    $i = 0
    while ($i -lt $Glob.Length) {
        $c = $Glob[$i]
        if ($Glob.Substring($i).StartsWith('**/')) {
            [void]$sb.Append('(?:[^/]+/)*')
            $i += 3
            continue
        }
        if ($c -eq '*') {
            [void]$sb.Append('[^/]*')
        }
        elseif ($c -eq '?') {
            [void]$sb.Append('[^/]')
        }
        else {
            [void]$sb.Append([regex]::Escape([string]$c))
        }
        $i++
    }
    [void]$sb.Append('$')
    return $sb.ToString()
}

function Get-ContentApplyPolicy {
    <#
    .SYNOPSIS
        Loads and validates config/website-content-apply-paths.json.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Content-apply policy not found: $Path"
    }
    $cfg = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json

    $maxBytes = 0
    if (-not [int]::TryParse([string]$cfg.max_total_bytes, [ref]$maxBytes) -or $maxBytes -le 0) {
        throw "Content-apply policy $Path has no positive integer max_total_bytes."
    }
    $maxFiles = 0
    if (-not [int]::TryParse([string]$cfg.max_files, [ref]$maxFiles) -or $maxFiles -le 0) {
        throw "Content-apply policy $Path has no positive integer max_files."
    }
    $allow = @($cfg.allow | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { [string]$_ })
    if ($allow.Count -eq 0) {
        throw "Content-apply policy $Path has an empty allow list; refusing to run with nothing allowed rather than guess."
    }
    $extensions = @($cfg.allowed_extensions | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { ([string]$_).ToLowerInvariant() })
    if ($extensions.Count -eq 0) {
        throw "Content-apply policy $Path has no allowed_extensions."
    }
    $deny = @($cfg.deny | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { [string]$_ })

    return [pscustomobject]@{
        MaxTotalBytes     = $maxBytes
        MaxFiles          = $maxFiles
        AllowedExtensions = $extensions
        Allow             = $allow
        AllowRegex        = @($allow | ForEach-Object { ConvertTo-ContentApplyGlobRegex -Glob $_ })
        Deny              = $deny
        DenyRegex         = @($deny | ForEach-Object { ConvertTo-ContentApplyGlobRegex -Glob $_ })
    }
}

function Test-ContentApplyRepoName {
    <#
    .SYNOPSIS
        Returns the reason a target repo name is refused, or $null when it is accepted.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][AllowNull()][string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return 'target_repo is required.'
    }
    if ($Name -cnotmatch $script:ContentApplyRepoPattern) {
        return "target_repo '$Name' does not match $($script:ContentApplyRepoPattern). This workflow writes with an org-wide token, so it refuses anything outside the lowercase FFC-EX-* fleet (a bare repo name, no owner, no URL)."
    }
    $suffix = $Name.Substring('FFC-EX-'.Length)
    if ($suffix -match '^[.-]|[.-]$' -or $suffix.Contains('..')) {
        return "target_repo '$Name' carries no usable domain after the FFC-EX- prefix (leading/trailing '.' or '-', or '..')."
    }
    return $null
}

function Test-ContentApplyIssueUrl {
    <#
    .SYNOPSIS
        Returns the reason an issue_url is refused, or $null (an empty value is accepted).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][AllowNull()][string]$Url)

    if ([string]::IsNullOrWhiteSpace($Url)) { return $null }
    if ($Url -cnotmatch '^https://github\.com/FreeForCharity/[A-Za-z0-9._-]+/issues/[0-9]+$') {
        return "issue_url '$Url' is not a FreeForCharity issue URL (https://github.com/FreeForCharity/<repo>/issues/<n>). It goes into the PR body as a link, so only a work-order issue in this org is accepted."
    }
    return $null
}

function Resolve-ContentApplyTitle {
    <#
    .SYNOPSIS
        Returns @{ Title; Error } for the PR title input (blank -> a default).
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][AllowNull()][string]$Title)

    $t = ([string]$Title).Trim()
    if ($t -eq '') {
        return [pscustomobject]@{ Title = 'Update site content'; Error = $null }
    }
    if ($t -match '[\x00-\x1F\x7F]') {
        return [pscustomobject]@{ Title = $null; Error = 'title contains a control character (newline, tab, ...). A PR title is one line.' }
    }
    if ($t.Length -gt 200) {
        return [pscustomobject]@{ Title = $null; Error = "title is $($t.Length) characters; the limit is 200." }
    }
    return [pscustomobject]@{ Title = $t; Error = $null }
}

function Test-ContentApplyPath {
    <#
    .SYNOPSIS
        Returns the reason a repo-relative path is refused, or $null when it may be written.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [AllowEmptyString()][AllowNull()][string]$Path,
        [Parameter(Mandatory = $true)]$Policy
    )

    if ([string]::IsNullOrEmpty($Path)) { return 'an empty path' }
    if ($Path.Contains('\')) { return "'$Path' uses a backslash; paths are repo-relative with '/' separators." }
    if ($Path.StartsWith('/') -or $Path -match '^[A-Za-z]:') { return "'$Path' is absolute; paths must be repo-relative." }
    if ($Path -cnotmatch $script:ContentApplyPathCharPattern) {
        return "'$Path' contains a character outside [A-Za-z0-9._/-]."
    }
    $segments = $Path.Split('/')
    foreach ($seg in $segments) {
        if ($seg -eq '') { return "'$Path' has an empty segment ('//' or a trailing '/')." }
        if ($seg -eq '..') { return "'$Path' contains '..'." }
        if ($seg -eq '.') { return "'$Path' contains a '.' segment." }
        # Hidden files and directories (.github, .husky, .env, ...) are tooling,
        # never content, at any depth.
        if ($seg.StartsWith('.')) { return "'$Path' has a hidden segment '$seg'; this workflow never writes dotfiles or dot-directories." }
    }
    if ($Path.Contains('..')) { return "'$Path' contains '..'." }

    # Hard refusals: no allowlist edit can reach these.
    $root = $segments[0]
    foreach ($denied in $script:ContentApplyDeniedRoots) {
        if ($root -ieq $denied) { return "'$Path' is under $denied/, which this workflow never writes." }
    }
    $base = $segments[-1]
    foreach ($denied in $script:ContentApplyDeniedBasenames) {
        if ($base -ieq $denied) { return "'$Path' is a $denied file, which this workflow never writes." }
    }
    if ($base -match '(?i)\.(ya?ml)$' -and $Path -match '(?i)(^|/)workflows/') {
        return "'$Path' looks like a workflow file, which this workflow never writes."
    }
    $ext = [System.IO.Path]::GetExtension($base).ToLowerInvariant()
    if ($Policy.AllowedExtensions -notcontains $ext) {
        return "'$Path' has extension '$ext'; only $($Policy.AllowedExtensions -join ', ') may be written (no binaries, no other code)."
    }

    foreach ($rx in $Policy.DenyRegex) {
        if ($Path -cmatch $rx) { return "'$Path' is on the deny list in config/website-content-apply-paths.json." }
    }
    $allowed = $false
    foreach ($rx in $Policy.AllowRegex) {
        if ($Path -cmatch $rx) { $allowed = $true; break }
    }
    if (-not $allowed) {
        return "'$Path' is not on the allowlist ($($Policy.Allow -join ', '))."
    }
    return $null
}

function Test-ContentApplyFileContent {
    <#
    .SYNOPSIS
        Returns the reasons a file's content is refused (empty array when accepted).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Content
    )

    $problems = [System.Collections.Generic.List[string]]::new()
    if ($Content.Contains([char]0)) {
        $problems.Add("'$Path' contains a NUL byte; only UTF-8 text may be written.")
        return , $problems.ToArray()
    }
    if ([string]::IsNullOrWhiteSpace($Content)) {
        $problems.Add("'$Path' is empty. Blanking a content file breaks the template's imports; leave the file out of the payload instead.")
    }
    $ext = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($ext -eq '.json') {
        try {
            $doc = [System.Text.Json.JsonDocument]::Parse($Content)
            $doc.Dispose()
        }
        catch {
            $problems.Add("'$Path' is not valid JSON: $($_.Exception.Message)")
        }
    }
    elseif ($ext -eq '.ts') {
        foreach ($name in $script:ContentApplyTsDenied.Keys) {
            if ($Content -match $script:ContentApplyTsDenied[$name]) {
                $problems.Add("'$Path' contains $name. Content modules are data; this workflow refuses code that runs at build time.")
            }
        }
        # Static imports may only reach the site's own files (relative or the
        # `@/` alias). No package can be added (manifests are refused), so an
        # import of one is either a mistake or a reach for something installed.
        $importRx = @(
            '(?m)^\s*import\s+(?:type\s+)?[^''";]*?\sfrom\s*[''"](?<spec>[^''"]+)[''"]',
            '(?m)^\s*import\s*[''"](?<spec>[^''"]+)[''"]',
            '(?m)^\s*export\s+(?:type\s+)?(?:\*|\{[^}]*\})\s*from\s*[''"](?<spec>[^''"]+)[''"]'
        ) -join '|'
        foreach ($m in [regex]::Matches($Content, $importRx)) {
            $spec = $m.Groups['spec'].Value
            if ($spec -notmatch '^(\./|\.\./|@/)') {
                $problems.Add("'$Path' imports '$spec'; content modules may only import the site's own files ('./', '../' or '@/').")
            }
        }
    }
    return , $problems.ToArray()
}

function Test-ContentApplyPayload {
    <#
    .SYNOPSIS
        Parses content_json and checks every path and file against the policy.
    .OUTPUTS
        [pscustomobject] with Errors (string[]), Files (Path, Bytes, Content), TotalBytes, PayloadBytes.
    #>
    [CmdletBinding()]
    param(
        [AllowEmptyString()][AllowNull()][string]$ContentJson,
        [Parameter(Mandatory = $true)]$Policy
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    $files = [System.Collections.Generic.List[object]]::new()
    $payloadBytes = 0
    # Every exit builds the same shape from the state at that point.
    $result = {
        [pscustomobject]@{
            Errors       = $errors.ToArray()
            Files        = $files.ToArray()
            TotalBytes   = [int](($files | Measure-Object -Property Bytes -Sum).Sum)
            PayloadBytes = $payloadBytes
        }
    }

    if ([string]::IsNullOrWhiteSpace($ContentJson)) {
        $errors.Add('content_json is required: a JSON object mapping repo-relative paths to file contents.')
        return & $result
    }
    $payloadBytes = [System.Text.Encoding]::UTF8.GetByteCount($ContentJson)
    if ($payloadBytes -gt $Policy.MaxTotalBytes) {
        $errors.Add("content_json is $payloadBytes bytes; the limit is $($Policy.MaxTotalBytes) bytes. Split the content into several runs.")
        return & $result
    }

    # System.Text.Json, not ConvertFrom-Json: ConvertFrom-Json keeps the LAST of
    # two identical keys and drops the first silently, so a payload could show
    # a reviewer one content for a path and write another.
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($ContentJson)
    }
    catch {
        $errors.Add("content_json is not valid JSON: $($_.Exception.Message)")
        return & $result
    }
    try {
        if ($doc.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
            $errors.Add("content_json must be a JSON object of path -> content; got a JSON $($doc.RootElement.ValueKind).")
            return & $result
        }
        $seen = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($prop in $doc.RootElement.EnumerateObject()) {
            $path = $prop.Name
            if ($seen.ContainsKey($path)) {
                $errors.Add("'$path' appears more than once (or collides with '$($seen[$path])' by case).")
                continue
            }
            $seen[$path] = $path
            $reason = Test-ContentApplyPath -Path $path -Policy $Policy
            if ($reason) { $errors.Add($reason); continue }
            if ($prop.Value.ValueKind -ne [System.Text.Json.JsonValueKind]::String) {
                $errors.Add("'$path' maps to a JSON $($prop.Value.ValueKind); each value must be the file's full content as a string.")
                continue
            }
            $content = $prop.Value.GetString()
            foreach ($p in (Test-ContentApplyFileContent -Path $path -Content $content)) { $errors.Add($p) }
            $files.Add([pscustomobject]@{
                    Path    = $path
                    Bytes   = [System.Text.Encoding]::UTF8.GetByteCount($content)
                    Content = $content
                })
        }
        if ($seen.Count -eq 0) {
            $errors.Add('content_json is an empty object; there is nothing to apply.')
        }
        if ($seen.Count -gt $Policy.MaxFiles) {
            $errors.Add("content_json names $($seen.Count) files; the limit is $($Policy.MaxFiles).")
        }
    }
    finally {
        $doc.Dispose()
    }
    return & $result
}

function Invoke-ContentApplyValidation {
    <#
    .SYNOPSIS
        Validates every 707 input at once. Both 707 jobs call this.
    .OUTPUTS
        [pscustomobject] Ok, Errors, TargetRepo, Title, IssueUrl, Files, TotalBytes, PayloadBytes.
    #>
    [CmdletBinding()]
    param(
        [AllowEmptyString()][AllowNull()][string]$TargetRepo,
        [AllowEmptyString()][AllowNull()][string]$ContentJson,
        [AllowEmptyString()][AllowNull()][string]$Title,
        [AllowEmptyString()][AllowNull()][string]$IssueUrl,
        [Parameter(Mandatory = $true)]$Policy
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    $repo = ([string]$TargetRepo).Trim()
    $repoReason = Test-ContentApplyRepoName -Name $repo
    if ($repoReason) { $errors.Add($repoReason) }

    $titleResult = Resolve-ContentApplyTitle -Title $Title
    if ($titleResult.Error) { $errors.Add($titleResult.Error) }

    $issue = ([string]$IssueUrl).Trim()
    $issueReason = Test-ContentApplyIssueUrl -Url $issue
    if ($issueReason) { $errors.Add($issueReason) }

    $payload = Test-ContentApplyPayload -ContentJson $ContentJson -Policy $Policy
    foreach ($e in $payload.Errors) { $errors.Add($e) }

    return [pscustomobject]@{
        Ok           = ($errors.Count -eq 0)
        Errors       = $errors.ToArray()
        TargetRepo   = $repo
        Title        = $titleResult.Title
        IssueUrl     = $issue
        Files        = $payload.Files
        TotalBytes   = $payload.TotalBytes
        PayloadBytes = $payload.PayloadBytes
    }
}

function Write-ContentApplyFileSet {
    <#
    .SYNOPSIS
        Writes validated files into a checkout and reports create/update/unchanged per file.
    .DESCRIPTION
        The paths were already checked by Test-ContentApplyPath; this is the
        second line, against the CHECKOUT rather than the string: the resolved
        path must stay under the repo root, and no existing component of it may
        be a symbolic link (a repo whose `src/data` linked to `.github/` would
        otherwise receive the write there). Content is written byte-for-byte as
        UTF-8 without a BOM.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Files
    )

    $root = [System.IO.Path]::GetFullPath($RepoPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $report = [System.Collections.Generic.List[object]]::new()
    foreach ($f in $Files) {
        $rel = [string]$f.Path
        $full = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($root, $rel))
        if (-not $full.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::Ordinal)) {
            throw "Refusing to write '$rel': it resolves to '$full', outside the checkout '$root'."
        }
        $cursor = $root
        foreach ($seg in $rel.Split('/')) {
            $cursor = Join-Path $cursor $seg
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force
                if ($item.LinkType -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                    throw "Refusing to write '$rel': '$cursor' is a symbolic link in the target repo."
                }
            }
        }
        if (Test-Path -LiteralPath $full -PathType Container) {
            throw "Refusing to write '$rel': a directory already exists at that path."
        }
        $status = 'create'
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $before = [System.IO.File]::ReadAllText($full, $utf8)
            $status = if ($before -ceq [string]$f.Content) { 'unchanged' } else { 'update' }
        }
        if ($PSCmdlet.ShouldProcess($rel, "write ($status)")) {
            $dir = Split-Path -Parent $full
            if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            [System.IO.File]::WriteAllText($full, [string]$f.Content, $utf8)
        }
        $report.Add([pscustomobject]@{ Path = $rel; Status = $status; Bytes = $f.Bytes })
    }
    return , $report.ToArray()
}

function Get-ContentApplyAutoMergePlan {
    <#
    .SYNOPSIS
        Decides whether 707 may enable auto-merge on the PR it opened, and with which method.
    .DESCRIPTION
        Auto-merge means "merge as soon as the branch's requirements pass". On a
        branch with NO required status checks that is "merge now", before the
        site's CI has reported, which is not what 707 promises. So auto-merge is
        enabled only when all of these hold:
          * the dispatch asked for it;
          * the repo allows auto-merge (allow_auto_merge);
          * the default branch requires at least one status check or workflow
            (a ruleset `required_status_checks` / `workflows` rule, or classic
            branch protection contexts);
          * a merge method is allowed by BOTH the repo settings and every
            ruleset `pull_request` rule's allowed_merge_methods.
        Anything else returns Enable = $false and the reason, which the workflow
        reports instead of failing: the PR is open either way.
    .PARAMETER Repo
        The REST repository object (allow_auto_merge, allow_squash_merge, ...).
    .PARAMETER Rules
        The array from GET /repos/{o}/{r}/rules/branches/{branch}.
    .PARAMETER ClassicRequiredChecks
        Context/check names from classic branch protection; empty when the branch is unprotected.
    .PARAMETER ClassicProtectionKnown
        $false when classic protection could not be read (e.g. 403); the plan then refuses rather than assume none.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][bool]$Requested,
        [Parameter(Mandatory = $true)]$Repo,
        [AllowEmptyCollection()][object[]]$Rules = @(),
        [AllowEmptyCollection()][string[]]$ClassicRequiredChecks = @(),
        [bool]$ClassicProtectionKnown = $true
    )

    $plan = { param($enable, $method, $reason) [pscustomobject]@{ Enable = $enable; Method = $method; Reason = $reason } }

    if (-not $Requested) { return & $plan $false $null 'auto_merge=false was requested; the PR is left open for review.' }
    if (-not [bool]$Repo.allow_auto_merge) {
        return & $plan $false $null 'auto-merge is not enabled on this repository (Settings > General > Allow auto-merge is off); the PR is left open to be merged by hand.'
    }

    $ruleChecks = @($Rules | Where-Object { $_.type -eq 'required_status_checks' } | ForEach-Object { @($_.parameters.required_status_checks) } | Where-Object { $_ })
    $ruleWorkflows = @($Rules | Where-Object { $_.type -eq 'workflows' } | ForEach-Object { @($_.parameters.workflows) } | Where-Object { $_ })
    $hasRequiredChecks = ($ruleChecks.Count + $ruleWorkflows.Count + @($ClassicRequiredChecks | Where-Object { $_ }).Count) -gt 0
    if (-not $hasRequiredChecks) {
        if (-not $ClassicProtectionKnown) {
            return & $plan $false $null 'classic branch protection could not be read, so whether CI is required is unknown; auto-merge was not enabled rather than risk merging before CI.'
        }
        return & $plan $false $null 'the default branch requires no status checks, so auto-merge would merge before the site CI reports; the PR is left open to be merged by hand once CI is green.'
    }

    $repoAllows = [ordered]@{
        squash = [bool]$Repo.allow_squash_merge
        merge  = [bool]$Repo.allow_merge_commit
        rebase = [bool]$Repo.allow_rebase_merge
    }
    $methods = @('squash', 'merge', 'rebase' | Where-Object { $repoAllows[$_] })
    foreach ($rule in @($Rules | Where-Object { $_.type -eq 'pull_request' })) {
        $allowedByRule = @($rule.parameters.allowed_merge_methods | Where-Object { $_ })
        if ($allowedByRule.Count -gt 0) {
            $methods = @($methods | Where-Object { $allowedByRule -contains $_ })
        }
    }
    if ($methods.Count -eq 0) {
        return & $plan $false $null 'no merge method is allowed by both the repository settings and its rulesets; the PR is left open.'
    }
    return & $plan $true $methods[0] "auto-merge enabled ($($methods[0])); the PR merges once the required checks pass."
}

function Get-ContentApplyLandDecision {
    <#
    .SYNOPSIS
        Decides whether 707 may merge the content PR it opened, from one poll of its checks.
    .DESCRIPTION
        FFC-EX repos have auto-merge off and require no status checks, so 707
        cannot hand landing to GitHub (see Get-ContentApplyAutoMergePlan). Instead
        it polls the PR head and merges itself, but only on positive evidence that
        the site's own CI passed. Returns Action = 'merge', 'wait' or 'leave', with
        a Reason:
          * 'leave' when the head moved since 707 pushed it, the PR is closed or
            already merged, a check run concluded anything but success / neutral /
            skipped, or a commit status is failure / error;
          * 'wait' while any check run is unfinished, any status is pending, no
            check or status exists yet, or GitHub has not computed mergeability;
          * 'merge' only when at least one check or status exists, all are
            finished and passing, and the PR is open, mergeable, and still at the
            sha 707 pushed.
        The caller turns a 'wait' that outlasts its window into 'leave'.
    .PARAMETER Pull
        The REST pull request object (state, merged, mergeable, head.sha).
    .PARAMETER ExpectedSha
        The commit 707 pushed.
    .PARAMETER CheckRuns
        check_runs from GET /repos/{o}/{r}/commits/{sha}/check-runs.
    .PARAMETER Statuses
        statuses from GET /repos/{o}/{r}/commits/{sha}/status.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Pull,
        [Parameter(Mandatory = $true)][string]$ExpectedSha,
        [AllowEmptyCollection()][object[]]$CheckRuns = @(),
        [AllowEmptyCollection()][object[]]$Statuses = @()
    )

    $d = { param($action, $reason) [pscustomobject]@{ Action = $action; Reason = $reason } }

    if ([string]$Pull.state -ne 'open' -or [bool]$Pull.merged) { return & $d 'leave' "the PR is no longer open (state=$($Pull.state), merged=$($Pull.merged))." }
    if ([string]$Pull.head.sha -ne $ExpectedSha) { return & $d 'leave' "the PR head moved from $ExpectedSha to $($Pull.head.sha) after 707 pushed; not merging someone else's commit." }

    $passing = @('success', 'neutral', 'skipped')
    $failed = @($CheckRuns | Where-Object { $_.status -eq 'completed' -and $passing -notcontains [string]$_.conclusion })
    if ($failed.Count -gt 0) {
        $names = ($failed | ForEach-Object { "$($_.name)=$($_.conclusion)" }) -join ', '
        return & $d 'leave' "site CI did not pass: $names."
    }
    $badStatus = @($Statuses | Where-Object { @('failure', 'error') -contains [string]$_.state })
    if ($badStatus.Count -gt 0) {
        $names = ($badStatus | ForEach-Object { "$($_.context)=$($_.state)" }) -join ', '
        return & $d 'leave' "a commit status failed: $names."
    }
    if (@($CheckRuns).Count + @($Statuses).Count -eq 0) { return & $d 'wait' 'no check run or status has reported on the PR head yet.' }
    $running = @($CheckRuns | Where-Object { $_.status -ne 'completed' })
    $pending = @($Statuses | Where-Object { $_.state -eq 'pending' })
    if ($running.Count + $pending.Count -gt 0) { return & $d 'wait' "$($running.Count + $pending.Count) check(s) still running." }
    if ($null -eq $Pull.mergeable) { return & $d 'wait' 'GitHub has not computed mergeability yet.' }
    if (-not [bool]$Pull.mergeable) { return & $d 'leave' "the PR is not mergeable (mergeable_state=$($Pull.mergeable_state))." }
    return & $d 'merge' "all $(@($CheckRuns).Count + @($Statuses).Count) check(s) passed on $ExpectedSha."
}

function Get-ContentApplyMergeRoute {
    <#
    .SYNOPSIS
        Decides how 707 lands a content PR whose checks passed: 'queue' or 'direct'. Pure.
    .DESCRIPTION
        A branch whose rules include `merge_queue` takes changes only through the
        queue, so a direct `PUT /repos/{o}/{r}/pulls/{n}/merge` is refused there (#1654). 44 of
        116 FFC-EX repos carried the repo-level "Merge Queue + Branch Up-to-Date"
        ruleset on 2026-10-12. Returns Route = 'queue' when any rule is
        `merge_queue`, 'direct' when the rules were read and none is, and 'unknown'
        when the rules could not be read: the caller then leaves the PR open
        rather than guess which route GitHub will accept.
    .PARAMETER Rules
        The array from GET /repos/{o}/{r}/rules/branches/{branch}.
    .PARAMETER RulesKnown
        $false when that read failed.
    #>
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()][object[]]$Rules = @(),
        [bool]$RulesKnown = $true
    )
    $r = { param($route, $reason) [pscustomobject]@{ Route = $route; Reason = $reason } }
    if (-not $RulesKnown) { return & $r 'unknown' "the branch rules could not be read, so whether it requires the merge queue is unknown." }
    if (@($Rules | Where-Object { $_.type -eq 'merge_queue' }).Count -gt 0) { return & $r 'queue' 'the branch requires the merge queue.' }
    return & $r 'direct' 'the branch has no merge queue rule.'
}

if ($MyInvocation.InvocationName -eq '.') { return }

Write-Error 'Dot-source this script to load the content-apply guard functions; it does nothing when run directly.'
exit 2
