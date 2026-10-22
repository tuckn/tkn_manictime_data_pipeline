// Active application seconds, never a net score or an inference of mental state.
const roleLabels = {focus:'集中', distracted:'だらけ', other:'その他', unknown:'用途未分類'};
function engagementChart(days) {
  const roles = Object.keys(roleLabels), weekdays = ['月','火','水','木','金','土','日'];
  const totals = Object.fromEntries(roles.map(k=>[k,0]));
  const counts = Array(7).fill(0);
  const values = Array.from({length:7},()=>Array.from({length:24},()=>Object.fromEntries(roles.map(k=>[k,0]))));
  let missing = 0;
  for (const day of days) {
    const row = D.daily[day], dow = (new Date(day+'T00:00:00Z').getUTCDay()+6)%7;
    if (!row.engagement_hourly) { missing++; continue; }
    counts[dow]++;
    for (const role of roles) row.engagement_hourly[role].forEach((seconds,h)=>{
      values[dow][h][role] += seconds; totals[role] += seconds;
    });
  }
  $('engagement-totals').innerHTML = roles.map(role=>'<div class="role-total '+role+'"><span>'+roleLabels[role]+'</span><strong>'+(totals[role]<3600?num(totals[role]/60)+' 分':hrs(totals[role]))+'</strong></div>').join('');
  $('engagement-coverage').textContent = '選択期間のActive中のアプリ時間の内訳。'+(D.combined?'複数PCは同時利用も加算します。':'')+(missing?'用途データのない日が '+missing+' 日あります。build-report --all で更新してください。':'');
  const averages = values.map((row,d)=>row.map(v=>Object.fromEntries(roles.map(k=>[k,v[k]/(counts[d]||1)/60]))));
  const max = Math.max(1,...averages.flatMap(row=>row.flatMap(v=>[v.focus,v.distracted])));
  const dot = (minutes,cx,role)=>'<circle class="'+role+(minutes?'':' zero')+'" cx="'+cx+'" cy="15" r="'+(minutes?12*Math.sqrt(minutes/max):1.5)+'"/>';
  let html = '<table class="focus-map"><caption>曜日別・1日あたりの平均時間（分）。各マスの左が集中、右がだらけ。</caption><thead><tr><th>曜日</th>'+Array.from({length:24},(_,h)=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>';
  const rows = [];
  weekdays.forEach((label,d)=>{
    html += '<tr><th scope="row">'+label+'<small>'+counts[d]+'日</small></th>';
    averages[d].forEach((v,h)=>{
      const description = label+'曜 '+h+'時台 / '+roles.map(k=>roleLabels[k]+' '+num(v[k])+'分').join(' / ')+' / 記録 '+counts[d]+'日';
      html += counts[d]?'<td><button type="button" data-day="'+d+'" data-hour="'+h+'" aria-label="'+description+'" title="'+description+'"><svg viewBox="0 0 56 30" aria-hidden="true">'+dot(v.focus,14,'focus')+dot(v.distracted,42,'distracted')+'</svg></button></td>':'<td class="no-record" aria-label="記録なし">—</td>';
      rows.push([label,h+'時',...roles.map(k=>counts[d]?Number(v[k].toFixed(2)):'記録なし'),counts[d]]);
    });
    html+='</tr>';
  });
  $('engagement-map').innerHTML=html+'</tbody></table>';
  $('engagement-values').innerHTML=table(['曜日','時間帯',...roles.map(k=>roleLabels[k]+' (分/日)'),'記録日数'],rows);
  $('engagement-scale').textContent='丸の面積 = 分数（青・橙は共通の尺度、最大 '+num(max)+'分/日）。小さな輪は0分、—は記録なし。';
  $('engagement-detail').textContent='マスを選ぶと、集中・だらけ・その他・未分類の分数を確認できます。';
  const describe = event=>{
    const button=event.target.closest('button[data-day]');
    if(button) $('engagement-detail').textContent=button.getAttribute('aria-label');
  };
  $('engagement-map').onclick=describe;
  for (const button of $('engagement-map').querySelectorAll('button')) button.onfocus=describe;
}
