/* global d3 */

const els = {
  pathInput: document.getElementById('pathInput'),
  scanBtn: document.getElementById('scanBtn'),
  status: document.getElementById('status'),
  viz: document.getElementById('viz'),
  details: document.getElementById('details'),
  driveList: document.getElementById('driveList'),
  browseDrives: document.getElementById('browseDrives'),
  maxDepth: document.getElementById('maxDepth'),
  excludeHidden: document.getElementById('excludeHidden'),
  followSymlinks: document.getElementById('followSymlinks'),
  modeBtns: Array.from(document.querySelectorAll('.mode-btn')),
  crumbs: document.getElementById('currentPath'),
};

function humanSize(bytes) {
  const thresh = 1024;
  if (Math.abs(bytes) < thresh) return bytes + ' B';
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let u = -1;
  do {
    bytes /= thresh; ++u;
  } while (Math.abs(bytes) >= thresh && u < units.length - 1);
  return bytes.toFixed(1) + ' ' + units[u];
}

function setStatus(text) { els.status.textContent = text || ''; }
function setCrumbs(text) { els.crumbs.textContent = text || ''; }

async function fetchDrives() {
  const res = await fetch('/api/drive_roots');
  const data = await res.json();
  els.driveList.innerHTML = '';
  (data.drives || []).forEach((d) => {
    const btn = document.createElement('div');
    btn.className = 'drive';
    btn.textContent = d;
    btn.addEventListener('click', () => { els.pathInput.value = d; });
    els.driveList.appendChild(btn);
  });
}

async function scan() {
  const path = els.pathInput.value || 'C:\\';
  const max_depth = parseInt(els.maxDepth.value || '50', 10);
  const exclude_hidden = !!els.excludeHidden.checked;
  const follow_symlinks = !!els.followSymlinks.checked;
  setStatus('Scanning...');
  setCrumbs('');
  try {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, max_depth, exclude_hidden, follow_symlinks }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    setStatus('');
    render(data);
  } catch (e) {
    console.error(e);
    setStatus('Error: ' + e.message);
  }
}

function colorForDepth(depth) {
  const palette = d3.interpolateRainbow;
  return d3.color(palette((depth * 0.12) % 1)).formatHex();
}

function render(rootData) {
  const mode = document.querySelector('.mode-btn.active')?.dataset.mode || 'circle';
  if (mode === 'circle') renderCirclePack(rootData); else renderSunburst(rootData);
}

function clearViz() { els.viz.innerHTML = ''; }

function renderCirclePack(rootData) {
  clearViz();
  const width = els.viz.clientWidth;
  const height = els.viz.clientHeight;

  const root = d3.pack()
    .size([width, height])
    .padding(2)(
      d3.hierarchy(rootData)
        .sum((d) => d.size || 0)
        .sort((a, b) => (b.value || 0) - (a.value || 0))
    );

  const svg = d3.select(els.viz).append('svg')
    .attr('viewBox', [0, 0, width, height])
    .attr('width', width)
    .attr('height', height)
    .attr('font-family', 'system-ui,Segoe UI,Roboto,Arial')
    .attr('text-anchor', 'middle');

  let focus = root;
  let view;

  const tooltip = d3.select(els.viz).append('div').attr('class', 'tooltip').style('opacity', 0);

  const color = (d) => colorForDepth(d.depth);

  const node = svg.append('g')
    .selectAll('circle')
    .data(root.descendants())
    .join('circle')
    .attr('fill', (d) => d.children ? color(d) : '#0d1330')
    .attr('stroke', '#24306b')
    .attr('stroke-width', 1)
    .on('mouseover', function (event, d) {
      d3.select(this).attr('stroke', '#6aa9ff');
      tooltip.style('opacity', 1).html(`${d.data.name}<br>${humanSize(d.value || 0)}`);
    })
    .on('mousemove', function (event) {
      tooltip.style('left', (event.offsetX + 12) + 'px').style('top', (event.offsetY + 12) + 'px');
    })
    .on('mouseout', function () {
      d3.select(this).attr('stroke', '#24306b');
      tooltip.style('opacity', 0);
    })
    .on('click', (event, d) => focus !== d && (zoom(event, d), event.stopPropagation()));

  const label = svg.append('g')
    .style('font', '12px system-ui')
    .attr('pointer-events', 'none')
    .attr('text-anchor', 'middle')
    .selectAll('text')
    .data(root.descendants())
    .join('text')
    .style('fill-opacity', (d) => d.parent === root ? 1 : 0)
    .style('display', (d) => d.parent === root ? 'inline' : 'none')
    .text((d) => d.data.name);

  zoomTo([root.x, root.y, root.r * 2]);
  setCrumbs(rootData.path || rootData.name);

  function zoomTo(v) {
    const k = Math.min(width, height) / v[2];
    view = v;
    label.attr('transform', (d) => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`);
    node.attr('transform', (d) => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`);
    node.attr('r', (d) => d.r * k);
  }

  function zoom(event, d) {
    focus = d;
    const transition = svg.transition()
      .duration(event.altKey ? 7500 : 750)
      .tween('zoom', () => {
        const i = d3.interpolateZoom(view, [focus.x, focus.y, focus.r * 2]);
        return (t) => zoomTo(i(t));
      });

    label
      .filter((l) => l.parent === focus || this.style?.display === 'inline')
      .transition(transition)
      .style('fill-opacity', (l) => l.parent === focus ? 1 : 0)
      .on('start', function (l) { if (l.parent === focus) this.style.display = 'inline'; })
      .on('end', function (l) { if (l.parent !== focus) this.style.display = 'none'; });

    setCrumbs((d.data && d.data.path) || d.ancestors().map((n) => n.data.name).reverse().join(' / '));
    els.details.textContent = `${d.data.path || d.data.name} — ${humanSize(d.value || 0)}`;
  }

  svg.on('click', (event) => zoom(event, root));
}

function renderSunburst(rootData) {
  clearViz();
  const width = els.viz.clientWidth;
  const height = els.viz.clientHeight;
  const radius = Math.min(width, height) / 2;

  const root = d3.partition()
    .size([2 * Math.PI, radius])(
      d3.hierarchy(rootData)
        .sum((d) => d.size || 0)
        .sort((a, b) => (b.value || 0) - (a.value || 0))
    );

  const svg = d3.select(els.viz).append('svg')
    .attr('viewBox', [-width / 2, -height / 2, width, height])
    .style('font', '12px system-ui');

  const arc = d3.arc()
    .startAngle((d) => d.x0)
    .endAngle((d) => d.x1)
    .padAngle(1 / radius)
    .padRadius(radius / 2)
    .innerRadius((d) => d.y0)
    .outerRadius((d) => d.y1 - 1);

  const tooltip = d3.select(els.viz).append('div').attr('class', 'tooltip').style('opacity', 0);

  const path = svg.append('g')
    .selectAll('path')
    .data(root.descendants())
    .join('path')
    .attr('fill', (d) => colorForDepth(d.depth))
    .attr('d', arc)
    .on('mouseover', function (event, d) {
      tooltip.style('opacity', 1).html(`${d.data.name}<br>${humanSize(d.value || 0)}`);
    })
    .on('mousemove', function (event) {
      tooltip.style('left', (event.offsetX + 12) + 'px').style('top', (event.offsetY + 12) + 'px');
    })
    .on('mouseout', function () { tooltip.style('opacity', 0); })
    .on('click', (event, p) => {
      event.stopPropagation();
      root.each((d) => {
        d.target = {
          x0: Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
          x1: Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
          y0: Math.max(0, d.y0 - p.y0),
          y1: Math.max(0, d.y1 - p.y0),
        };
      });

      const t = svg.transition().duration(750);
      path.transition(t)
        .tween('data', (d) => {
          const i = d3.interpolate(d.current || d, d.target);
          return (t) => (d.current = i(t));
        })
        .attrTween('d', (d) => () => arc(d.current));

      setCrumbs(p.data.path || p.ancestors().map((n) => n.data.name).reverse().join(' / '));
      els.details.textContent = `${p.data.path || p.data.name} — ${humanSize(p.value || 0)}`;
    });

  root.each((d) => (d.current = d));

  setCrumbs(rootData.path || rootData.name);
}

// UI wiring
els.scanBtn.addEventListener('click', scan);
els.browseDrives.addEventListener('click', fetchDrives);
els.modeBtns.forEach((btn) => {
  btn.addEventListener('click', () => {
    els.modeBtns.forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    if (window.__lastData) render(window.__lastData);
  });
});

function render(data) { window.__lastData = data; const mode = document.querySelector('.mode-btn.active')?.dataset.mode || 'circle'; if (mode === 'circle') renderCirclePack(data); else renderSunburst(data); }

// Initial
fetchDrives();


