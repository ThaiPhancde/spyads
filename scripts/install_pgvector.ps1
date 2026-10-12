# Installs pgvector 0.8.6 into the local PostgreSQL 18 (needs an elevated PowerShell: it writes under D:\PostgreSQL\18).
# Source of the binary: https://github.com/andreiramani/pgvector_pgsql_windows (community build — pgvector has no official
# Windows binaries). Pass -Zip to use a zip you already downloaded / built; check its SHA256 first.
#   Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File D:\ToolSpy\scripts\install_pgvector.ps1'
param(
  [string]$Pg = "D:\PostgreSQL\18",
  [string]$Zip = "",
  [string]$ExpectedDllSha256 = "800c2ee572657e76144e0f3b809b56dad7c48725d37ba40255d60adcfd54ec35"
)
$ErrorActionPreference = "Stop"
$tmp = Join-Path $env:TEMP "pgvector_install"; Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory $tmp | Out-Null
if (-not $Zip) {
  $Zip = Join-Path $tmp "pg18.zip"
  Invoke-WebRequest "https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/0.8.6_18/vector.v0.8.6-pg18.zip" -OutFile $Zip
}
Expand-Archive $Zip (Join-Path $tmp "x")
$x = Join-Path $tmp "x"
$sha = (Get-FileHash (Join-Path $x "lib\vector.dll") -Algorithm SHA256).Hash.ToLower()
if ($ExpectedDllSha256 -and $sha -ne $ExpectedDllSha256) { throw "vector.dll SHA256 $sha does not match the one reviewed ($ExpectedDllSha256)" }
Copy-Item (Join-Path $x "lib\vector.dll") (Join-Path $Pg "lib") -Force
Copy-Item (Join-Path $x "share\extension\*") (Join-Path $Pg "share\extension") -Force
Copy-Item (Join-Path $x "include\server\extension\*") (Join-Path $Pg "include\server\extension") -Recurse -Force -ErrorAction SilentlyContinue
"installed vector.dll ($sha) + extension files into $Pg"
$env:PGPASSWORD = "admin"
& "$Pg\bin\psql.exe" -U postgres -h localhost -d toolspy -c "CREATE EXTENSION IF NOT EXISTS vector;" -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
