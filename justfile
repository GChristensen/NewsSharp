# Deployment to the Linux server over Samba.
#
#   just run                            start the server in a new cmd window
#   just build                          clean install of the frontend packages and a frontend build
#   just test                           backend and frontend tests
#   just deploy                         copy the code to the server
#   just pull-data                      replace local data/ with the server's
#   just remote='\\host\share\dir' deploy   use another installation directory

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# The installation directory on the server (holds the code, data/, config.yaml and .venv).
remote := env("NEWSSHARP_REMOTE", '\\server\share\newssharp')

# robocopy: 2-second timestamps for Samba, few retries, quiet; exit codes below 8 mean success
rc := "/FFT /XJ /R:2 /W:2 /NP /NDL /NJH"
ok := "; if ($LASTEXITCODE -ge 8) { exit 1 } else { exit 0 }"

_default:
    @just --list

# Start the web and worker processes (manage.py serve) in a new cmd window, which stays open after it exits.
run:
    Start-Process cmd.exe -WorkingDirectory '{{justfile_directory()}}' -ArgumentList '/k', 'title NewsSharp && uv run python manage.py serve'

# The frontend is built into frontend/dist, which whitenoise serves as is (no collectstatic).

# Clean install of the frontend packages, then build the frontend into frontend/dist.
[working-directory('frontend')]
build:
    npm ci
    npm run build

# Run the backend tests, then the frontend type check and unit tests.
test:
    uv run pytest -q
    cd frontend; npm run typecheck; if ($LASTEXITCODE -ne 0) { exit 1 }; npm test

# Copy the code and the built frontend to the server. data/, config.yaml and .venv there are not touched.
deploy:
    if (-not (Test-Path -LiteralPath '{{remote}}')) { throw 'Server directory not reachable: {{remote}}' }
    if (-not (Test-Path frontend\dist\index.html)) { throw 'frontend\dist is missing: run "just build" first.' }
    foreach ($d in 'config', 'accounts', 'feeds', 'reading', 'api', 'worker', 'deploy') { robocopy $d "{{remote}}\$d" /MIR /XD __pycache__ {{rc}}; if ($LASTEXITCODE -ge 8) { exit 1 } }; exit 0
    robocopy frontend\dist '{{remote}}\frontend\dist' /MIR {{rc}}{{ok}}
    robocopy . '{{remote}}' manage.py pyproject.toml uv.lock README.md config.example.yaml {{rc}}{{ok}}
    @echo 'Deployed. Restart the server (it applies migrations); if pyproject.toml dependencies changed, run "uv sync" there first.'

# Replace local data/ with the server's copy. The image cache, collected static files and the worker lock are not copied.
pull-data:
    if (-not (Test-Path -LiteralPath '{{remote}}\data')) { throw 'Server data not reachable: {{remote}}\data' }
    if (Test-Path data\newssharp.sqlite3) { try { [IO.File]::Open("$PWD\data\newssharp.sqlite3", 'Open', 'ReadWrite', 'None').Close() } catch { throw 'The local database is in use: stop the local server first.' } }
    if (Test-Path data) { Remove-Item -LiteralPath data -Recurse -Force }
    robocopy '{{remote}}\data' data /E /XD '{{remote}}\data\image_cache' '{{remote}}\data\static' /XF worker.lock {{rc}}{{ok}}
