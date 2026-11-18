# B-2 docker compose smoke: run a real debate inside containers
# Prereq: docker compose up -d and backend healthy
# ASCII-only output (Windows PowerShell 5.1 decodes UTF-8 scripts as ANSI otherwise).

$ErrorActionPreference = "Stop"
$base = "http://localhost"

Write-Host "[B2] 1) health check (nginx -> backend /api/health)"
$health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 20
Write-Host "[B2] health = $($health | ConvertTo-Json -Compress)"

Write-Host "[B2] 2) register + login (login sets the httpOnly cookie)"
$uname = "b2smoke_$(Get-Date -Format 'HHmmss')"
$reg = Invoke-RestMethod -Uri "$base/api/auth/register" -Method Post `
    -ContentType "application/json" -Body (@{username=$uname; password="password8"} | ConvertTo-Json)
Write-Host "[B2] registered id=$($reg.id)"
$login = Invoke-RestMethod -Uri "$base/api/auth/login" -Method Post `
    -ContentType "application/json" -Body (@{username=$uname; password="password8"} | ConvertTo-Json) `
    -SessionVariable websession
Write-Host "[B2] logged in as=$($login.username)"

Write-Host "[B2] 3) list builtin topics (reuse cookie session)"
$topics = Invoke-RestMethod -Uri "$base/api/topics" -TimeoutSec 20 -WebSession $websession
$topic = $topics.items | Where-Object { $_.is_builtin } | Select-Object -First 1
Write-Host "[B2] topic id=$($topic.id) title=$($topic.title)"
if (-not $topic) { throw "No builtin topic (seed failed?)" }

Write-Host "[B2] 4) start lite debate"
$debate = Invoke-RestMethod -Uri "$base/api/debates" -Method Post `
    -ContentType "application/json" -Body (@{topic_id=$topic.id; format="lite"} | ConvertTo-Json) `
    -WebSession $websession
Write-Host "[B2] debate id=$($debate.id) status=$($debate.status)"

Write-Host "[B2] 5) poll terminal status (max 10 min; GLM free tier is slow)"
$deadline = (Get-Date).AddMinutes(10)
$last = ""
while ((Get-Date) -lt $deadline) {
    $detail = Invoke-RestMethod -Uri "$base/api/debates/$($debate.id)" -TimeoutSec 20 -WebSession $websession
    $last = $detail.status
    if ($last -in @("finished", "failed", "stopped")) { break }
    Start-Sleep -Seconds 5
}
Write-Host "[B2] final status = $last"
if ($last -ne "finished") {
    Write-Host "[B2] FAIL: not finished, detail = $($detail | ConvertTo-Json -Depth 5 -Compress)"
    exit 1
}
$speeches = Invoke-RestMethod -Uri "$base/api/debates/$($debate.id)/speeches" -TimeoutSec 20 -WebSession $websession
Write-Host "[B2] PASS: finished, speeches=$($speeches.items.Count), winner=$($detail.winner)"
