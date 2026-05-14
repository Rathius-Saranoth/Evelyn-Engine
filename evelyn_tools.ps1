<#
.SYNOPSIS
    Evelyn Engine -- Interactive Tool Launcher

.DESCRIPTION
    Menu-driven launcher for Evelyn Engine developer tools.
    Designed to be run locally or over SSH from a phone/tablet.

    Tools are defined in the $TOOLS array at the top of this script.
    Add new entries there as new tools are created -- no other changes needed.

.USAGE
    From project root:
        .\evelyn_tools.ps1

    Over SSH (via alias):
        evelyn
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# =============================================================================
# Configuration
# =============================================================================

$PYTHON      = "C:\Users\ricky\AppData\Local\Programs\Python\Python311\python.exe"
$PROJECT_DIR = "C:\Projects\LocalAI"

# Review queue paths -- keep in sync with evelyn_config.py if they ever change
$EXTRACTED_DIR = "G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Entries\Extracted"
$PENDING_DIR   = "G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Entries\Pending"

# Tool registry -- add new tools here.
# Each entry: @{ Label = "Display name"; Script = "relative\path\to\script.py"; Desc = "One-line description" }
$TOOLS = @(
    @{
        Label  = "Context Reviewer"
        Script = "Evelyn\tools\context_reviewer.py"
        Desc   = "Review auto-extracted EX_*.md facts (Extracted/ folder)"
    },
    @{
        Label  = "Pending Reviewer"
        Script = "Evelyn\tools\pending_reviewer.py"
        Desc   = "Review consolidation and recategorization proposals (Pending/ folder)"
    },
    @{
        Label  = "Apply Keyword Tags"
        Script = "Evelyn\tools\apply_keyword_tags.py"
        Desc   = "Backfill kw/ tags to vault files from gist keywords"
    },
    @{
        Label  = "Benchmark RAG"
        Script = "Evelyn\tools\benchmark_rag.py"
        Desc   = "Run RAG retrieval benchmark against the golden test set"
    }
)

# =============================================================================
# Terminal helpers
# =============================================================================

$ESC    = [char]27
$RESET  = "$ESC[0m"
$BOLD   = "$ESC[1m"
$DIM    = "$ESC[2m"
$GREEN  = "$ESC[92m"
$YELLOW = "$ESC[93m"
$CYAN   = "$ESC[96m"
$RED    = "$ESC[91m"

$BAR = "=" * 60
$DIV = "-" * 60

function Show-Header {
    Clear-Host
    Write-Host "$BOLD$CYAN$BAR$RESET"
    Write-Host "$BOLD$CYAN  Evelyn Engine -- Tool Launcher$RESET"
    Write-Host "$BOLD$CYAN$BAR$RESET"
    Write-Host ""
}

function Show-Menu {
    param([int]$Selected)

    Show-Header

    for ($i = 0; $i -lt $TOOLS.Count; $i++) {
        $tool   = $TOOLS[$i]
        $num    = $i + 1
        $label  = $tool.Label
        $desc   = $tool.Desc

        if ($i -eq $Selected) {
            Write-Host "  $GREEN[$num]$RESET $BOLD$label$RESET"
            Write-Host "       $DIM$desc$RESET"
        } else {
            Write-Host "  $DIM[$num]$RESET $label"
            Write-Host "       $DIM$desc$RESET"
        }
        Write-Host ""
    }

    Write-Host "$DIM$DIV$RESET"
    Write-Host "  Enter a number (1-$($TOOLS.Count)) and press Enter, or ${RED}Q$RESET to quit."
    Write-Host "$DIM$DIV$RESET"
}

# =============================================================================
# Pending counts (displayed on launch -- optional, non-blocking)
# =============================================================================

function Get-PendingCounts {
    <#
    .SYNOPSIS
        Returns a hashtable of quick file counts for the review queues.
        Silently returns 0s if the vault path is unreachable (e.g. Drive not mounted).
    #>
    $counts = @{ Extracted = 0; Consolidations = 0; Recategorizations = 0 }

    if (Test-Path $EXTRACTED_DIR) {
        $counts.Extracted = @(Get-ChildItem -Path $EXTRACTED_DIR -Filter "EX_*.md" -ErrorAction SilentlyContinue).Count
    }
    if (Test-Path $PENDING_DIR) {
        $counts.Consolidations    = @(Get-ChildItem -Path $PENDING_DIR -Filter "CONSOLIDATION_*.md" -ErrorAction SilentlyContinue).Count
        $counts.Recategorizations = @(Get-ChildItem -Path $PENDING_DIR -Filter "RECATEGORIZE_*.md"  -ErrorAction SilentlyContinue).Count
    }

    return $counts
}

# =============================================================================
# Main loop
# =============================================================================

Set-Location $PROJECT_DIR

# Show quick queue counts before drawing the menu
$counts = Get-PendingCounts
Show-Header
Write-Host "  $CYAN Queue Status$RESET"
Write-Host "  $DIM-----------------------------------------$RESET"
Write-Host "  Extracted facts  (context_reviewer) : $BOLD$YELLOW$($counts.Extracted)$RESET"
Write-Host "  Consolidations   (pending_reviewer) : $BOLD$YELLOW$($counts.Consolidations)$RESET"
Write-Host "  Recategorizations(pending_reviewer) : $BOLD$YELLOW$($counts.Recategorizations)$RESET"
Write-Host ""
Write-Host "  Press Enter to continue..."
$null = Read-Host

while ($true) {
    Show-Menu -Selected -1

    $raw = Read-Host "  Selection"
    $input = $raw.Trim().ToLower()

    if ($input -eq "q" -or $input -eq "quit" -or $input -eq "exit") {
        Clear-Host
        Write-Host "$DIM  Goodbye.$RESET`n"
        break
    }

    $num = 0
    if (-not [int]::TryParse($input, [ref]$num) -or $num -lt 1 -or $num -gt $TOOLS.Count) {
        Write-Host "  $RED[X] Invalid selection. Enter 1-$($TOOLS.Count) or Q.$RESET"
        Start-Sleep -Milliseconds 900
        continue
    }

    $tool       = $TOOLS[$num - 1]
    $scriptPath = Join-Path $PROJECT_DIR $tool.Script

    if (-not (Test-Path $scriptPath)) {
        Write-Host "  $RED[X] Script not found: $scriptPath$RESET"
        Start-Sleep -Milliseconds 1500
        continue
    }

    Clear-Host
    Write-Host "$BOLD$CYAN$BAR$RESET"
    Write-Host "$BOLD$CYAN  Launching: $($tool.Label)$RESET"
    Write-Host "$BOLD$CYAN$BAR$RESET"
    Write-Host ""

    # Run the tool; inherit stdin/stdout so interactive prompts pass through
    & $PYTHON $scriptPath

    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "$DIM$DIV$RESET"
    if ($exitCode -eq 0 -or $null -eq $exitCode) {
        Write-Host "  $GREEN[OK] $($tool.Label) exited cleanly.$RESET"
    } else {
        Write-Host "  $YELLOW[!!] $($tool.Label) exited with code $exitCode.$RESET"
    }
    Write-Host "$DIM$DIV$RESET"
    Write-Host ""
    Write-Host "  Press Enter to return to the menu..."
    $null = Read-Host
}
