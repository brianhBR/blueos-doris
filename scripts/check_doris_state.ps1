$base = "http://192.168.68.75:6040"

# Read DORIS_STATE param via PARAM_REQUEST_READ + cached PARAM_VALUE
function Read-Param {
  param([string]$Name)
  $body = @{
    header  = @{ system_id = 255; component_id = 0; sequence = 0 }
    message = @{
      type            = "PARAM_REQUEST_READ"
      target_system   = 1
      target_component = 1
      param_id        = ([char[]]$Name.PadRight(16, [char]0))
      param_index     = -1
    }
  } | ConvertTo-Json -Depth 10 -Compress
  curl.exe -s --max-time 5 -X POST -H "Content-Type: application/json" --data $body "$base/mavlink" | Out-Null
  Start-Sleep -Milliseconds 600
  $resp = curl.exe -s --max-time 5 "$base/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
  try {
    $j = $resp | ConvertFrom-Json
    $id = -join ($j.message.param_id | Where-Object { $_ -ne "`0" })
    Write-Host ("  {0,-18} = {1}  (cache id={2})" -f $Name, $j.message.param_value, $id)
  } catch { Write-Host "  $Name read failed: $resp" }
}

# Latest NAMED_VALUE_FLOAT from autopilot lua
Write-Host "=== Latest NAMED_VALUE_FLOAT ==="
$resp = curl.exe -s --max-time 5 "$base/mavlink/vehicles/1/components/1/messages/NAMED_VALUE_FLOAT"
try {
  $j = $resp | ConvertFrom-Json
  $name = -join ($j.message.name | Where-Object { $_ -ne "`0" })
  Write-Host "  name=$name value=$($j.message.value) freq=$($j.status.time.frequency) Hz last=$($j.status.time.last_update)"
} catch { Write-Host "  parse failed: $resp" }

Write-Host ""
Write-Host "=== DORIS params (read-back) ==="
Read-Param "DORIS_STATE"
Read-Param "DORIS_START"
Read-Param "DORIS_PRF_ID"
Read-Param "DORIS_INJ_LEAK"
Read-Param "SCR_ENABLE"

Write-Host ""
Write-Host "=== Autopilot HEARTBEAT ==="
$resp = curl.exe -s --max-time 5 "$base/mavlink/vehicles/1/components/1/messages/HEARTBEAT"
try {
  $j = $resp | ConvertFrom-Json
  Write-Host "  base_mode bits=$($j.message.base_mode.bits) status=$($j.message.system_status.type) last=$($j.status.time.last_update)"
} catch { Write-Host "  parse failed: $resp" }
