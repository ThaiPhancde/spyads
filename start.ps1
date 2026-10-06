# Chạy Market Intelligence OS local (API :8000 + Web :3000). Dữ liệu thật: bắt đầu từ trang "Tìm sản phẩm".
$root = $PSScriptRoot
$api = Join-Path $root "apps/api"
$web = Join-Path $root "apps/web"
# Nạp biến môi trường từ .env (nếu có) — các process con sẽ thừa hưởng
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
      $k, $v = $line.Split("=", 2)
      $v = ($v -replace "\s+#.*$", "").Trim().Trim('"')
      if ($v) { [Environment]::SetEnvironmentVariable($k.Trim(), $v, "Process") }
    }
  }
  Write-Host "Đã nạp $envFile"
}
if (-not (Test-Path "$api/.venv")) {
  python -m venv "$api/.venv"
  & "$api/.venv/Scripts/python.exe" -m pip install -r "$api/requirements.txt"
}
if (-not (Test-Path "$web/node_modules")) { Push-Location $web; npm install; Pop-Location }
Start-Process -WorkingDirectory $api -FilePath "$api/.venv/Scripts/python.exe" -ArgumentList "-m","uvicorn","app.main:app","--port","8000"
Start-Process -WorkingDirectory $web -FilePath "npm.cmd" -ArgumentList "run","dev"
Write-Host "API: http://localhost:8000/docs   Web: http://localhost:3000/search"
