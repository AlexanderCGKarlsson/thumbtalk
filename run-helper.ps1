# Resolve uv explicitly and leave the caller's working directory unchanged.
$thumbtalkUv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $thumbtalkUv) {
    [Console]::Error.WriteLine('ThumbTalk source startup requires uv: https://docs.astral.sh/uv/getting-started/installation/')
    exit 127
}

$thumbtalkProject = Join-Path $PSScriptRoot 'helper'
$thumbtalkEntry = Join-Path $thumbtalkProject 'thumbtalk_cli.py'
& $thumbtalkUv.Source run --project $thumbtalkProject --frozen --python 3.12 python $thumbtalkEntry @args
exit $LASTEXITCODE
