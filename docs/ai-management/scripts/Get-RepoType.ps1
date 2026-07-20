function Get-RepoType {
    <#
    .SYNOPSIS
        Determines the repo type from the FFC naming convention.

    .DESCRIPTION
        Returns a repo type string based on the repository name pattern:
        - "powershell-infra" for infrastructure/automation repos
        - "base" for everything else (external charity sites, internal tools, etc.)

    .PARAMETER RepoName
        The repository name to classify.

    .EXAMPLE
        Get-RepoType -RepoName "FFC-EX-legioninthewoods.org"
        # Returns: "base"

    .EXAMPLE
        Get-RepoType -RepoName "FFC-Cloudflare-Automation"
        # Returns: "powershell-infra"
    #>

    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoName
    )

    # PowerShell infrastructure repos (automation, static site tools, agents)
    $infraRepos = @(
        "FFC-Cloudflare-Automation",
        "FFC-Static-Site-Capture-Tools",
        "FFC-IN-Antigravity-Static-site-agent"
    )

    if ($infraRepos -contains $RepoName) {
        return "powershell-infra"
    }

    # External charity websites (GitHub Pages sites)
    if ($RepoName -like "FFC-EX-*") {
        return "base"
    }

    # Internal tools (usually Next.js or other web apps)
    if ($RepoName -like "FFC-IN-*") {
        return "base"
    }

    # Known standalone repos
    $standaloneRepos = @(
        "freeforcharity-web",
        "FreeForCharity.org",
        "TechnologyMonastery.org"
    )

    if ($standaloneRepos -contains $RepoName) {
        return "base"
    }

    # Default
    return "base"
}
