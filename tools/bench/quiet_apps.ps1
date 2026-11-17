# Demotes noisy background apps to BelowNormal for bench idle sweeps.
# Reversible by reboot; priorities are recorded to
# tools/bench/results/priority-restore.json only by run_big_ac.py, so a
# manual bench run does not need the restore step (reboot reverts).
$names = 'firefox', 'ferdium', 'everything', 'chrome', 'msedge', 'code'
$procs = Get-Process -Name $names -ErrorAction SilentlyContinue
$done = 0
foreach ($p in $procs) {
    try {
        if ($p.PriorityClass -ne 'BelowNormal') { $p.PriorityClass = 'BelowNormal' }
        $done++
    } catch {}
}
$seen = ($procs | Select-Object -ExpandProperty ProcessName -Unique) -join ', '
Write-Host ("demoted {0} processes: {1}" -f $done, $seen)
