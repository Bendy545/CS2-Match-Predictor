function switchTab(tab) {
    document.getElementById('room-tab').classList.toggle('hidden', tab !== 'room');
    document.getElementById('manual-tab').classList.toggle('hidden', tab !== 'manual');
    document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('results').classList.add('hidden');
    document.getElementById('error').textContent = '';
}

async function predictRoom() {
    const room = document.getElementById('room-url').value.trim();
    if (!room) return showError('Enter a room URL or match ID');

    showLoading();
    try {
        const res = await fetch('/api/predict-room', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({room})
        });
        const data = await res.json();
        if (!res.ok) return showError(data.error);
        showResults(data);
    } catch(e) {
        showError('Server error');
    }
}

async function predictManual() {
    const team1 = [
        document.getElementById('t1p1').value.trim(),
        document.getElementById('t1p2').value.trim(),
        document.getElementById('t1p3').value.trim(),
        document.getElementById('t1p4').value.trim(),
        document.getElementById('t1p5').value.trim(),
    ];
    const team2 = [
        document.getElementById('t2p1').value.trim(),
        document.getElementById('t2p2').value.trim(),
        document.getElementById('t2p3').value.trim(),
        document.getElementById('t2p4').value.trim(),
        document.getElementById('t2p5').value.trim(),
    ];
    const map = document.getElementById('map').value;

    if (team1.some(n => !n) || team2.some(n => !n)) return showError('Fill all 10 players');

    showLoading();
    try {
        const res = await fetch('/api/predict', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({team1, team2, map})
        });
        const data = await res.json();
        if (!res.ok) return showError(data.error);
        showResults(data);
    } catch(e) {
        showError('Server error');
    }
}

function showResults(data) {
    document.getElementById('error').textContent = '';

    const t1Name = data.team1_name || 'Team 1';
    const t2Name = data.team2_name || 'Team 2';
    const winner = data.winner === 'Team 1' ? t1Name : t2Name;

    document.getElementById('winner-text').textContent = winner + ' wins (' + data.confidence + '% probability)';
    document.getElementById('score-text').textContent = 'Estimated score: ' + data.estimated_score + ' | Score diff: ' + data.score_diff + ' rounds | Map: ' + data.map;

    document.getElementById('t1-label').textContent = t1Name;
    document.getElementById('t1-elo').textContent = data.team1.avg_elo;
    document.getElementById('t1-kd').textContent = data.team1.avg_kd;
    document.getElementById('t1-wr').textContent = data.team1.avg_wr + '%';

    document.getElementById('t2-label').textContent = t2Name;
    document.getElementById('t2-elo').textContent = data.team2.avg_elo;
    document.getElementById('t2-kd').textContent = data.team2.avg_kd;
    document.getElementById('t2-wr').textContent = data.team2.avg_wr + '%';

    const t1Players = data.team1.players.map(p => p.nickname + ' (' + Math.round(p.elo) + ')').join(', ');
    const t2Players = data.team2.players.map(p => p.nickname + ' (' + Math.round(p.elo) + ')').join(', ');
    document.getElementById('players-info').textContent = t1Name + ': ' + t1Players + ' | ' + t2Name + ': ' + t2Players;

    document.getElementById('results').classList.remove('hidden');
}

function showError(msg) {
    document.getElementById('error').textContent = msg;
    document.getElementById('results').classList.add('hidden');
}

function showLoading() {
    document.getElementById('error').textContent = 'Loading... (fetching 10 players from FACEIT API, this takes ~15 seconds)';
    document.getElementById('results').classList.add('hidden');
}