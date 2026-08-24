[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ModulePath,

    [string]$PyCharmHome = "",

    [string]$InspectionProfile = "",

    [string]$OutputPath = "",

    [switch]$ShowInspectorOutput
)

$ErrorActionPreference = "Stop"

function Convert-ToFileUrlPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return $Path.Replace("\", "/")
}

function Resolve-PyCharmHome {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Resolve-Path -LiteralPath $RequestedPath -ErrorAction SilentlyContinue
        if (-not $resolved) {
            throw "PyCharm installation was not found: $RequestedPath"
        }
        $candidates = @(Get-Item -LiteralPath $resolved.Path)
    }
    else {
        $roots = @(
            (Join-Path $env:ProgramFiles "JetBrains"),
            (Join-Path $env:LOCALAPPDATA "JetBrains\Installations"),
            (Join-Path $env:LOCALAPPDATA "Programs")
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
        $candidates = @(
            foreach ($root in $roots) {
                Get-ChildItem -LiteralPath $root -Directory -Filter "PyCharm*" -ErrorAction SilentlyContinue
            }
        )
    }

    $valid = @(
        $candidates |
            Where-Object {
                (Test-Path -LiteralPath (Join-Path $_.FullName "bin\inspect.bat") -PathType Leaf) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName "jbr\bin\java.exe") -PathType Leaf)
            } |
            Sort-Object FullName -Descending
    )
    if ($valid.Count -eq 0) {
        throw "A usable PyCharm installation was not found. Specify -PyCharmHome explicitly."
    }
    return $valid[0].FullName
}

function Get-MissingInspectionTools {
    param([Parameter(Mandatory = $true)][string[]]$LogPaths)

    $tools = @(
        foreach ($logPath in $LogPaths) {
            if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
                continue
            }
            $content = Get-Content -LiteralPath $logPath -Raw
            $matches = [regex]::Matches(
                $content,
                "Descriptions are missed for tools:\s*(?<tools>.*?)(?:\r?\n\s*>?\s*java\.lang\.Throwable|$)",
                [System.Text.RegularExpressions.RegexOptions]::Singleline
            )
            foreach ($match in $matches) {
                ($match.Groups["tools"].Value -replace "\r?\n", " " -replace ">", "") -split "," |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ -match "^[A-Za-z0-9_]+$" }
            }
        }
    )
    return @($tools | Select-Object -Unique)
}

function New-CompatibleInspectionProfile {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [Parameter(Mandatory = $true)][string[]]$MissingTools
    )

    [xml]$profileXml = Get-Content -LiteralPath $SourcePath -Raw
    $profile = $profileXml.component.profile
    if (-not $profile) {
        throw "Inspection profile has no profile element: $SourcePath"
    }
    foreach ($toolName in $MissingTools) {
        $tool = @($profile.inspection_tool | Where-Object { $_.class -eq $toolName }) | Select-Object -First 1
        if (-not $tool) {
            $tool = $profileXml.CreateElement("inspection_tool")
            $tool.SetAttribute("class", $toolName)
            $profile.AppendChild($tool) | Out-Null
        }
        $tool.SetAttribute("enabled", "false")
        $tool.SetAttribute("enabled_by_default", "false")
        if (-not $tool.level) {
            $tool.SetAttribute("level", "WARNING")
        }
    }
    $profileXml.Save($DestinationPath)
}

function Invoke-Inspection {
    param(
        [Parameter(Mandatory = $true)][string]$InspectBatPath,
        [Parameter(Mandatory = $true)][string]$ProjectPath,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ReportPath,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Module,
        [switch]$ShowOutput
    )

    New-Item -ItemType Directory -Path $ReportPath -Force | Out-Null
    Get-ChildItem -LiteralPath $ReportPath -Filter "*.xml" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    & $InspectBatPath $ProjectPath $ProfilePath $ReportPath -v2 -d $Module *> $LogPath
    $exitCode = $LASTEXITCODE
    if ($ShowOutput -and (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        Get-Content -LiteralPath $LogPath
    }
    return $exitCode
}

function Write-IsolatedSdkFiles {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$SdkName
    )

    $optionsPath = Join-Path $ConfigPath "options"
    New-Item -ItemType Directory -Path $optionsPath -Force | Out-Null

    $projectUrl = Convert-ToFileUrlPath $ProjectRoot
    $pythonUrl = Convert-ToFileUrlPath $PythonPath
    $pythonRoot = Split-Path -Parent (Split-Path -Parent $PythonPath)
    $pythonRootUrl = Convert-ToFileUrlPath $pythonRoot
    $userPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311"
    $userPythonRootUrl = Convert-ToFileUrlPath $userPythonRoot

    $jdkTable = @"
<application>
  <component name="ProjectJdkTable">
    <jdk version="2">
      <name value="$SdkName" />
      <type value="Python SDK" />
      <version value="Python 3.11.8" />
      <homePath value="$pythonUrl" />
      <roots>
        <classPath>
          <root type="composite">
            <root url="file://$userPythonRootUrl/DLLs" type="simple" />
            <root url="file://$userPythonRootUrl/Lib" type="simple" />
            <root url="file://$userPythonRootUrl" type="simple" />
            <root url="file://$projectUrl/venv" type="simple" />
            <root url="file://$projectUrl/venv/Lib/site-packages" type="simple" />
            <root url="file://$pythonRootUrl" type="simple" />
            <root url="file://`$APPLICATION_HOME_DIR`$/plugins/python-ce/helpers/typeshed/stdlib" type="simple" />
          </root>
        </classPath>
        <sourcePath>
          <root type="composite" />
        </sourcePath>
      </roots>
      <additional ASSOCIATED_PROJECT_PATH="$projectUrl" SDK_UUID="00000000-0000-0000-0000-000000000001">
        <setting name="FLAVOR_ID" value="VirtualEnvSdkFlavor" />
        <setting name="FLAVOR_DATA" value="{}" />
      </additional>
    </jdk>
  </component>
</application>
"@

    $sdkSettings = @"
<application>
  <component name="PySdkSettings">
    <option name="PREFERRED_VIRTUALENV_BASE_PATH" value="$projectUrl/venv" />
    <option name="PREFERRED_VIRTUALENV_BASE_SDK" value="$userPythonRootUrl/python.exe" />
  </component>
</application>
"@

    Set-Content -LiteralPath (Join-Path $optionsPath "jdk.table.xml") -Value $jdkTable -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $optionsPath "pySdk.xml") -Value $sdkSettings -Encoding UTF8
}

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$module = Resolve-Path (Join-Path $projectRoot $ModulePath) -ErrorAction Stop
$modulePath = $module.Path
$projectPrefix = $projectRoot.TrimEnd("\") + "\"
if ($modulePath -ne $projectRoot -and -not $modulePath.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Module path must be inside the project root: $projectRoot"
}

$pythonPath = Join-Path $projectRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python SDK was not found: $pythonPath"
}

$miscPath = Join-Path $projectRoot ".idea\misc.xml"
if (-not (Test-Path -LiteralPath $miscPath -PathType Leaf)) {
    throw "Project SDK settings were not found: $miscPath"
}
[xml]$misc = Get-Content -LiteralPath $miscPath -Raw
$sdkName = [string]$misc.project.component.Where({ $_.name -eq "ProjectRootManager" })."project-jdk-name"
if (-not $sdkName) {
    throw "Project Python SDK name was not found in: $miscPath"
}

$PyCharmHome = Resolve-PyCharmHome $PyCharmHome
$inspectBat = Join-Path $PyCharmHome "bin\inspect.bat"
$jbrPath = Join-Path $PyCharmHome "jbr"
if (-not (Test-Path -LiteralPath $inspectBat -PathType Leaf)) {
    throw "PyCharm Inspection script was not found: $inspectBat"
}
if (-not (Test-Path -LiteralPath (Join-Path $jbrPath "bin\java.exe") -PathType Leaf)) {
    throw "PyCharm bundled runtime was not found: $jbrPath"
}

$profileWasExplicit = [bool]$InspectionProfile
if (-not $InspectionProfile) {
    $InspectionProfile = Join-Path $projectRoot ".idea\inspectionProfiles\Project_Default.xml"
}
if (-not (Test-Path -LiteralPath $InspectionProfile -PathType Leaf)) {
    throw "Inspection profile was not found: $InspectionProfile"
}

$inspectionRoot = Join-Path $projectRoot ".ide-inspection"
if (-not $OutputPath) {
    $leaf = Split-Path $modulePath -Leaf
    if (Test-Path -LiteralPath $modulePath -PathType Leaf) {
        $leaf = [System.IO.Path]::GetFileNameWithoutExtension($leaf)
    }
    $OutputPath = Join-Path $inspectionRoot ("reports\{0}" -f $leaf)
}
$output = [System.IO.Path]::GetFullPath($OutputPath)
$inspectionConfig = Join-Path $inspectionRoot "config"
$inspectionSystem = Join-Path $inspectionRoot "system"
$inspectionLog = Join-Path $inspectionSystem "log"
$inspectionPlugins = Join-Path $inspectionSystem "plugins"
$ideaLogPath = Join-Path $inspectionLog "idea.log"
$propertiesPath = Join-Path $inspectionRoot "pycharm.properties"
$runLogPath = Join-Path $inspectionRoot "inspect.log"
$compatibleProfilePath = Join-Path $inspectionRoot "compatible-profile.xml"
$profileForRun = $InspectionProfile
$usingCompatibleProfile = $false

if (-not $profileWasExplicit -and
    (Test-Path -LiteralPath $compatibleProfilePath -PathType Leaf) -and
    (Get-Item -LiteralPath $compatibleProfilePath).LastWriteTime -ge
        (Get-Item -LiteralPath $InspectionProfile).LastWriteTime) {
    $profileForRun = $compatibleProfilePath
    $usingCompatibleProfile = $true
}

New-Item -ItemType Directory -Path $inspectionRoot, $inspectionConfig, $inspectionSystem -Force | Out-Null
Write-IsolatedSdkFiles $inspectionConfig $projectRoot $pythonPath $sdkName
$inspectionConfigUrl = Convert-ToFileUrlPath $inspectionConfig
$inspectionSystemUrl = Convert-ToFileUrlPath $inspectionSystem
$inspectionPluginsUrl = Convert-ToFileUrlPath $inspectionPlugins
$inspectionLogUrl = Convert-ToFileUrlPath $inspectionLog
Set-Content -LiteralPath $propertiesPath -Encoding UTF8 -Value @"
idea.config.path=$inspectionConfigUrl
idea.system.path=$inspectionSystemUrl
idea.plugins.path=$inspectionPluginsUrl
idea.log.path=$inspectionLogUrl
"@

$previousJdk = $env:PYCHARM_JDK
$previousProperties = $env:PYCHARM_PROPERTIES
$compatibleProfile = $null
try {
    $env:PYCHARM_JDK = $jbrPath
    $env:PYCHARM_PROPERTIES = $propertiesPath
    Remove-Item -LiteralPath $ideaLogPath -Force -ErrorAction SilentlyContinue

    $exitCode = Invoke-Inspection `
        -InspectBatPath $inspectBat `
        -ProjectPath $projectRoot `
        -ProfilePath $profileForRun `
        -ReportPath $output `
        -LogPath $runLogPath `
        -Module $modulePath `
        -ShowOutput:$ShowInspectorOutput

    $missingTools = if ($usingCompatibleProfile) {
        @()
    }
    else {
        @(Get-MissingInspectionTools @($runLogPath, $ideaLogPath))
    }
    $hasInspectionReports = @(
        Get-ChildItem -LiteralPath $output -Filter "*.xml" -File -ErrorAction SilentlyContinue |
            Where-Object BaseName -ne ".descriptions"
    ).Count -gt 0
    if ($missingTools.Count -gt 0 -and (-not $hasInspectionReports -or $exitCode -ne 0)) {
        $compatibleProfile = if ($profileWasExplicit) {
            Join-Path $inspectionRoot ("compatible-profile-{0}.xml" -f $PID)
        }
        else {
            $compatibleProfilePath
        }
        New-CompatibleInspectionProfile $InspectionProfile $compatibleProfile $missingTools
        $profileForRun = $compatibleProfile
        $usingCompatibleProfile = $true
        Write-Output ("Retrying with a compatible profile ({0} unsupported inspections disabled)." -f $missingTools.Count)
        $exitCode = Invoke-Inspection `
            -InspectBatPath $inspectBat `
            -ProjectPath $projectRoot `
            -ProfilePath $compatibleProfile `
            -ReportPath $output `
            -LogPath $runLogPath `
            -Module $modulePath `
            -ShowOutput:$ShowInspectorOutput
    }

    if ($exitCode -ne 0) {
        throw "PyCharm Inspection failed with exit code $exitCode. See: $runLogPath"
    }
}
finally {
    if ($profileWasExplicit -and $compatibleProfile -and
        (Test-Path -LiteralPath $compatibleProfile -PathType Leaf)) {
        Remove-Item -LiteralPath $compatibleProfile -Force -ErrorAction SilentlyContinue
    }
    $env:PYCHARM_JDK = $previousJdk
    $env:PYCHARM_PROPERTIES = $previousProperties
}

$reports = Get-ChildItem -LiteralPath $output -Filter "*.xml" -File -ErrorAction SilentlyContinue |
    Where-Object BaseName -ne ".descriptions"
$rows = foreach ($report in $reports) {
    [xml]$xml = Get-Content -LiteralPath $report.FullName -Raw
    foreach ($problem in @($xml.problems.problem)) {
        [pscustomobject]@{
            Inspection = $report.BaseName
            File       = [string]$problem.file
            Line       = [int]$problem.line
            Message    = ([string]$problem.description -replace "<[^>]+>", "")
        }
    }
}

$codeRows = @($rows | Where-Object Inspection -ne "SpellCheckingInspection")
Write-Output "Report: $output"
Write-Output ("Warnings: {0}" -f $codeRows.Count)
if ($codeRows.Count) {
    foreach ($row in ($codeRows | Sort-Object File, Line, Inspection)) {
        $file = $row.File -replace '^file://\$PROJECT_DIR\$/', ''
        Write-Output ("{0}:{1} [{2}] {3}" -f $file, $row.Line, $row.Inspection, $row.Message)
    }
}
