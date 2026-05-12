param([int]$MaxMin = 14)

$base = "http://192.168.68.75:8095"
$since = 0

# Show what's currently buffered
$resp = curl.exe -s --max-time 8 "$base/api/v1/tracker/iridium-status?since_id=0"
$data = $resp | ConvertFrom-Json
foreach ($m in $data.messages) {
  $ts = $m.timestamp.Substring(11, 8)
  Write-Host ("  [{0}] id={1,-3} sev={2} text={3}" -f $ts, $m.id, $m.severity, $m.text)
}
$since = $data.latest_id
Write-Host "-- tailing since id=$since (max ${MaxMin} min) --"

$deadline = (Get-Date).AddMinutes($MaxMin)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 3
  $resp = curl.exe -s --max-time 8 "$base/api/v1/tracker/iridium-status?since_id=$since"
  if (-not $resp) { continue }
  try { $data = $resp | ConvertFrom-Json } catch { Write-Host "parse error: $resp"; continue }
  foreach ($m in $data.messages) {
    $ts = $m.timestamp.Substring(11, 8)
    Write-Host ("  [{0}] id={1,-3} sev={2} text={3}" -f $ts, $m.id, $m.severity, $m.text)
    if ($m.text -like "*IRIDIUM*" -and ($m.text -like "*PASSED*" -or $m.text -like "*FAILED*")) {
      Write-Host "`nresult: $($m.text)"
      exit 0
    }
  }
  if ($data.messages.Count -gt 0) { $since = $data.latest_id }
}
Write-Host "timeout (no PASSED/FAILED)"
exit 1
