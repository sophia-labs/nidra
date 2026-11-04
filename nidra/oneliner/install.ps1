# Nidra for Windows: ask your databases questions in plain words.
#
#   irm https://nidra.computer/install.ps1 | iex
#
# What this does: checks that Docker runs, asks for your OpenRouter key (unless OPENROUTER_API_KEY is set),
# starts one container, ghcr.io/sophia-labs/nidra, with your data in a Docker volume called `nidra`, waits
# until it answers and prints the link to open. Running it again updates Nidra and keeps your data.
# Everything is reachable from this computer only (127.0.0.1).
#
# Settings, all optional: $env:NIDRA_MODEL (an OpenRouter model id), $env:NIDRA_PORT (8086), $env:NIDRA_API_PORT (3001),
# $env:NIDRA_NO_OPEN=1 (print the link, do not open a browser).
& {
  $Image = if ($env:NIDRA_IMAGE) { $env:NIDRA_IMAGE } else { 'ghcr.io/sophia-labs/nidra:latest' }
  $Name = if ($env:NIDRA_NAME) { $env:NIDRA_NAME } else { 'nidra' }
  $Volume = if ($env:NIDRA_VOLUME) { $env:NIDRA_VOLUME } else { 'nidra' }
  $Port = if ($env:NIDRA_PORT) { $env:NIDRA_PORT } else { '8086' }
  $ApiPort = if ($env:NIDRA_API_PORT) { $env:NIDRA_API_PORT } else { '3001' }

  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Nidra runs in Docker. Install Docker Desktop (https://docs.docker.com/get-docker/), start it, then run this again.'; return
  }
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Write-Host 'Docker is installed but not running. Start Docker Desktop, then run this again.'; return }

  if (-not $env:OPENROUTER_API_KEY) {
    $secret = Read-Host 'Your OpenRouter key (sk-or-..., from https://openrouter.ai/keys)' -AsSecureString
    $env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
  }
  if (-not $env:OPENROUTER_API_KEY) { Write-Host 'No key given.'; return }

  Write-Host 'Getting Nidra (the first time this downloads about 0.8 GB)...'
  docker pull $Image *> $null
  if ($LASTEXITCODE -ne 0) {
    docker image inspect $Image *> $null
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not download $Image."; return }
  }
  if (docker ps -aq --filter "name=^$Name$") {
    Write-Host "Replacing the Nidra container you had (your data stays in the volume $Volume)."
    docker rm -f $Name *> $null
  }
  # docker reads the key from the environment (-e OPENROUTER_API_KEY): it never appears in a command line.
  $run = @('run', '-d', '--name', $Name, '--restart', 'unless-stopped', '--stop-timeout', '30',
    '-p', "127.0.0.1:${Port}:8086", '-p', "127.0.0.1:${ApiPort}:3001", '-v', "${Volume}:/data",
    '-e', 'OPENROUTER_API_KEY', '-e', "NIDRA_URL=http://localhost:$Port")
  if ($env:NIDRA_MODEL) { $run += @('-e', "NIDRA_MODEL=$($env:NIDRA_MODEL)") }
  docker @run $Image | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host 'Nidra did not start (is the port taken? set $env:NIDRA_PORT).'; return }

  Write-Host -NoNewline 'Starting'
  for ($i = 0; ; $i++) {
    docker exec $Name curl -sf -m 3 http://127.0.0.1:3001/api/health/live *> $null
    if ($LASTEXITCODE -eq 0) { break }
    if ((docker inspect -f '{{.State.Running}}' $Name) -ne 'true') {
      Write-Host ''; docker logs --tail 30 $Name; Write-Host 'Nidra stopped. Its last words are above.'; return
    }
    if ($i -ge 300) { Write-Host ''; Write-Host "Nidra did not answer within five minutes; see: docker logs $Name"; return }
    Write-Host -NoNewline '.'; Start-Sleep -Seconds 1
  }
  $url = (docker exec $Name nidra url).Trim()
  Write-Host "`n`nNidra is running. Open this link (it holds your key; keep it to yourself):`n`n  $url`n"
  Write-Host 'It reads the sample database first (Chinook, a music store); give it a minute or two.'
  Write-Host "Stop: docker stop $Name   Start: docker start $Name   The link again: docker exec $Name nidra url"
  if (-not $env:NIDRA_NO_OPEN) { Start-Process $url }
}
