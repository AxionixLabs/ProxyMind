[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ModulePath,

    [string]$PyCharmHome = "C:\Program Files\JetBrains\PyCharm 2025.3.1",

    [string]$InspectionProfile = "",

    [string]$OutputPath = "",

    [switch]$ShowInspectorOutput
)

$ErrorActionPreference = "Stop"

function Convert-ToFileUrlPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return $Path.Replace("\", "/")
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

$inspectBat = Join-Path $PyCharmHome "bin\inspect.bat"
$jbrPath = Join-Path $PyCharmHome "jbr"
if (-not (Test-Path -LiteralPath $inspectBat -PathType Leaf)) {
    throw "PyCharm Inspection script was not found: $inspectBat"
}
if (-not (Test-Path -LiteralPath (Join-Path $jbrPath "bin\java.exe") -PathType Leaf)) {
    throw "PyCharm bundled runtime was not found: $jbrPath"
}

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
$propertiesPath = Join-Path $inspectionRoot "pycharm.properties"
$runLogPath = Join-Path $inspectionRoot "inspect.log"

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

New-Item -ItemType Directory -Path $output -Force | Out-Null
Get-ChildItem -LiteralPath $output -Filter "*.xml" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force

$previousJdk = $env:PYCHARM_JDK
$previousProperties = $env:PYCHARM_PROPERTIES
try {
    $env:PYCHARM_JDK = $jbrPath
    $env:PYCHARM_PROPERTIES = $propertiesPath

    if ($ShowInspectorOutput) {
        & $inspectBat $projectRoot $InspectionProfile $output -v2 -d $modulePath
    }
    else {
        & $inspectBat $projectRoot $InspectionProfile $output -v2 -d $modulePath *> $runLogPath
    }

    if ($LASTEXITCODE -ne 0) {
        throw "PyCharm Inspection failed with exit code $LASTEXITCODE. See: $runLogPath"
    }
}
finally {
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
Write-Output "Inspection report: $output"
Write-Output ("Code warnings: {0}" -f $codeRows.Count)
if ($codeRows.Count) {
    $codeRows | Sort-Object File, Line | Format-Table -AutoSize -Wrap
}
