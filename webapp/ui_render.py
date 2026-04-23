from __future__ import annotations

import html

"""
UI render helpers for the webapp.

Function index:
- render_output_browser_page: render output-directory browser page.
- render_home_page_html: render main home page HTML/CSS/JS.
"""


def render_output_browser_page(job_id: int, rel_dir: str, rows_html: str) -> str:
    """Render the output browser page for a specific run."""
    safe_job_id = html.escape(str(job_id))
    safe_rel_dir = html.escape(rel_dir)
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;max-width:980px;margin:20px auto'>"
        f"<h3>Output Browser - Job #{safe_job_id}</h3>"
        f"<p>Folder: <code>{safe_rel_dir}</code></p>"
        "<p><a href='/'>Back</a></p>"
        + rows_html
        + "</body></html>"
    )


def render_home_page_html(
    *,
    paused: bool,
    msg: str,
    counts: dict[str, int],
    video_dir_posix: str,
    summary_table_page_size: int,
    summary_pagination_bits: list[str],
    summary_rows: list[str],
    frame_results_page_size: int,
    pagination_bits: list[str],
    result_rows: list[str],
    job_items: list[str],
    output_label: str,
    input_label: str,
    video_label: str,
    hide_blanks: bool,
    species_mode: str,
    has_active: bool,
    records_json: str,
    detector_min_confidence: float,
    suppress_blank_species_boxes: bool,
) -> str:
    """Render the full home page HTML/CSS/JS template from prepared view data."""
    safe_msg = html.escape(msg or "")
    return f"""<!doctype html>
<html><head><meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>Wildlife Processing Console</title>
<style>
body{{font-family:Inter,Segoe UI,Arial,sans-serif;background:#f6f8fb;color:#1e293b;margin:0}}
.wrap{{max-width:1200px;margin:0 auto;padding:20px}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.title{{font-size:28px;font-weight:700}}
.badge{{padding:6px 10px;border-radius:999px;background:{'#fef3c7' if paused else '#dcfce7'};color:#111827;font-weight:600}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.panel{{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04);overflow-x:hidden}}
.counts{{display:grid;grid-template-columns:repeat(5,minmax(80px,1fr));gap:8px;margin-top:8px}}
.count{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px;text-align:center}}
.count b{{display:block;font-size:20px}}
label{{font-size:13px;font-weight:600;color:#334155}}
input{{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box}}
.btn{{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:8px;background:#0f172a;color:white;text-decoration:none;border:0;cursor:pointer;font:inherit;line-height:1.2;appearance:none;-webkit-appearance:none}}
.btn-compact{{padding:6px 10px;font-size:13px}}
.btn-subtle{{background:#334155}}
.btn:visited{{color:#fff}}
.actions{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.actions a{{margin-right:0}}
.msg{{margin:8px 0;color:#0f766e}}
.jobs{{margin-top:14px;display:grid;grid-template-columns:1fr;gap:12px}}
.job-card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:12px}}
.job-head{{display:flex;justify-content:space-between;align-items:center}}
.status{{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase}}
.st-queued{{background:#e2e8f0}} .st-running{{background:#bfdbfe}} .st-done{{background:#bbf7d0}} .st-error{{background:#fecaca}} .st-cancelled{{background:#f1f5f9}}
.job-meta{{font-size:12px;color:#64748b;margin-top:4px}}
.job-log{{margin-top:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px;font-family:Consolas,monospace;font-size:12px}}
.job-err{{margin-top:8px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:8px;color:#991b1b;font-family:Consolas,monospace;font-size:12px}}
.preview{{margin-top:10px;max-width:360px;border:1px solid #cbd5e1;border-radius:8px}}
.job-actions{{margin-top:10px}}
.progress{{height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin-top:8px}}
.bar{{height:100%;background:#3b82f6}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px}}
.tbl th,.tbl td{{border:1px solid #e2e8f0;padding:8px;text-align:left}}
.tbl th{{background:#f8fafc}}
.table-scroll{{width:100%;max-width:100%;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;box-sizing:border-box}}
.table-scroll .tbl{{min-width:760px}}
.table-scroll .tbl th,.table-scroll .tbl td{{white-space:nowrap}}
.thumb{{max-width:220px;border:1px solid #cbd5e1;border-radius:6px}}
.results-list{{display:grid;gap:10px}}
.result-card{{display:grid;grid-template-columns:240px 1fr;gap:12px;align-items:start;padding:10px;border:1px solid #e2e8f0;border-radius:10px;background:#fff}}
.result-text{{display:grid;gap:5px;font-size:13px}}
.desc-col{{max-width:100%;color:#334155}}
.tag-list{{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}}
.tag-chip{{display:inline-block;padding:2px 8px;border:1px solid #c7d2fe;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:12px}}
.tag-chip.default{{border-color:#bbf7d0;background:#dcfce7;color:#166534}}
.tag-chip-filter{{cursor:pointer;user-select:none}}
.tag-chip-filter.active{{background:#3730a3;color:#fff;border-color:#3730a3}}
.browser-row{{display:grid;grid-template-columns:minmax(0,280px) minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}}
.browser-row > div{{min-width:0}}
.video-list{{max-height:340px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;box-sizing:border-box}}
.frame-list{{max-height:340px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;box-sizing:border-box}}
.video-item,.frame-item{{display:block;width:100%;text-align:left;padding:7px 8px;border:1px solid #dbe3ef;border-radius:6px;background:#f8fafc;color:#0f172a;cursor:pointer;margin-bottom:6px;box-sizing:border-box;overflow-wrap:anywhere}}
.video-item.active,.frame-item.active{{background:#dbeafe;border-color:#93c5fd}}
.frame-item .job-meta{{margin-top:2px}}
.inline-preview{{border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;display:grid;gap:8px;min-width:0;max-width:100%;max-height:340px;overflow:auto;box-sizing:border-box}}
.inline-preview .job-meta{{overflow-wrap:anywhere;word-break:break-word}}
.inline-preview img{{display:block;width:100%;max-width:100%;height:auto;max-height:min(260px,42vh);object-fit:contain;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}
.tab-btn{{padding:8px 10px;border-radius:8px;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;cursor:pointer}}
.tab-btn.active{{background:#3730a3;color:#fff;border-color:#3730a3}}
.app-modal{{position:fixed;inset:0;z-index:10000;display:none;align-items:center;justify-content:center}}
.app-modal.show{{display:flex}}
.app-modal-backdrop{{position:absolute;inset:0;background:rgba(15,23,42,.55)}}
.app-modal-box{{position:relative;z-index:1;background:#fff;border-radius:12px;padding:14px;max-width:min(720px,94vw);max-height:min(88vh,900px);overflow:auto;box-shadow:0 10px 40px rgba(0,0,0,.2)}}
.app-modal-title{{font-size:19px;font-weight:700;margin-bottom:8px;color:#0f172a}}
.app-modal-body{{margin-bottom:10px;font-size:15px;color:#334155;line-height:1.35;white-space:pre-line}}
.app-modal-actions{{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}}
.enqueue-row{{display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;background:#f8fafc}}
.enqueue-row.disabled{{opacity:.65}}
.enqueue-meta{{font-size:12px;color:#64748b}}
.viewer-overlay{{position:fixed;inset:0;background:rgba(2,6,23,.72);display:none;align-items:center;justify-content:center;z-index:9999}}
.viewer-box{{width:min(96vw,1200px);height:min(92vh,900px);background:#0b1220;border-radius:10px;padding:10px;display:grid;grid-template-rows:auto auto 1fr;gap:8px}}
.viewer-top{{display:flex;justify-content:space-between;align-items:center;color:#e2e8f0}}
.viewer-controls{{display:flex;gap:8px;align-items:center;color:#e2e8f0}}
.viewer-canvas{{overflow:auto;background:#020617;border:1px solid #1e293b;border-radius:8px;display:flex;align-items:flex-start;justify-content:flex-start}}
.viewer-img{{transform-origin:top left;max-width:none;max-height:none}}
.preview-table{{width:100%;border-collapse:collapse;margin-top:8px}}
.preview-table th,.preview-table td{{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;font-size:13px}}
.preview-table th{{background:#f8fafc}}
.preview-scroll{{max-height:52vh;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;background:#fff}}
.preview-table{{min-width:1650px}}
.export-preview-box{{max-width:min(1100px,96vw)}}
@media (max-width: 980px) {{
  .wrap{{padding:12px}}
  .row{{grid-template-columns:1fr}}
  .counts{{grid-template-columns:repeat(2,minmax(120px,1fr))}}
  .browser-row{{grid-template-columns:1fr}}
  .result-card{{grid-template-columns:1fr}}
  .thumb{{max-width:100%}}
  .actions{{gap:6px}}
  .btn,.tab-btn{{width:100%}}
  .app-modal-box{{max-width:96vw;max-height:86vh}}
  .panel{{border-radius:12px}}
  .actions{{row-gap:8px}}
  .job-meta{{line-height:1.35}}
  .tbl th,.tbl td{{padding:7px;font-size:12px}}
}}
@media (max-width: 560px) {{
  .title{{font-size:22px}}
  .badge{{font-size:12px}}
  .panel{{padding:12px}}
  input{{padding:10px;font-size:14px}}
  .job-meta{{font-size:12px}}
  .tab-btn{{font-size:13px;padding:8px}}
  .viewer-box{{width:98vw;height:94vh;padding:8px}}
  .counts{{grid-template-columns:1fr}}
  .actions{{flex-direction:column;align-items:stretch;gap:8px}}
  .actions .btn,.actions .tab-btn{{width:100%}}
  .result-text{{gap:7px}}
  .tag-list{{gap:8px}}
  .preview-scroll{{max-height:60vh}}
  .preview-table th,.preview-table td{{font-size:12px;padding:5px 6px}}
  .app-modal-box.export-preview-box{{max-width:98vw;padding:10px}}
}}
</style></head>
<body><div class='wrap'>
<div class='top'><div class='title'>Wildlife Processing Console</div><div class='badge'>{'Paused' if paused else 'Running'}</div></div>
<div class='msg'>{safe_msg}</div>
<div class='row'>
  <div class='panel'>
    <h3 style='margin-top:0'>Queue Control</h3>
    <div class='actions'>
      <a class='btn btn-subtle js-action' href='/pause'>Pause</a>
      <a class='btn btn-subtle js-action' href='/resume'>Resume</a>
      <a class='btn btn-subtle js-action' href='/cancel-all' data-confirm='Cancel all queued and running jobs?

This keeps your existing job history and generated files.'>Cancel All</a>
      <button class='btn btn-subtle' type='button' onclick='manualSyncNow()'>Sync View</button>
      <a class='btn btn-subtle' href='/' >Refresh</a>
    </div>
    <div class='counts'>
      <div class='count'><small>Queued</small><b>{counts.get('queued',0)}</b></div>
      <div class='count'><small>Running</small><b>{counts.get('running',0)}</b></div>
      <div class='count'><small>Done</small><b>{counts.get('done',0)}</b></div>
      <div class='count'><small>Error</small><b>{counts.get('error',0)}</b></div>
      <div class='count'><small>Cancelled</small><b>{counts.get('cancelled',0)}</b></div>
    </div>
  </div>
  <div class='panel'>
    <h3 style='margin-top:0'>New Job</h3>
    <form method='post' enctype='multipart/form-data' action='/process'>
      <label>Media file (image/video)</label><input type='file' name='media' required />
      <div style='height:8px'></div>
      <label>Frame rate (video)</label><input type='number' step='0.1' value='1' name='fps'/>
      <div style='height:8px'></div>
      <label>ML URL</label><input name='ml_url' value='http://127.0.0.1:8010'/>
      <div style='height:8px'></div>
      <label>Species URL</label><input name='species_url' value='http://127.0.0.1:8100'/>
      <div style='height:10px'></div>
      <button class='btn' type='submit'>Queue Job</button>
    </form>
    <div style='height:10px'></div>
    <h4 style='margin:6px 0'>Quick Batch Upload (files/folder/drag-drop)</h4>
    <input id='multiFiles' type='file' multiple webkitdirectory />
    <div style='height:8px'></div>
    <div id='dropZone' style='border:2px dashed #94a3b8;border-radius:10px;padding:12px;color:#334155'>
      Drag & drop files here
    </div>
    <div style='height:8px'></div>
    <button class='btn' type='button' onclick='queueSelectedFiles()'>Add Selected Files to Queue</button>
  </div>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Batch Queue From Folder</h3>
  <form id='enqueueFolderForm' onsubmit='return false;'>
    <label>Folder Path (local on this machine)</label>
    <input id='enqueueFolderPath' name='folder_path' value='{video_dir_posix}' />
    <p class='job-meta' style='margin:6px 0 0 0'>If this folder changes, the app will ask whether to keep default output or set a custom output path.</p>
    <div style='height:8px'></div>
    <label>Include extensions (comma-separated)</label>
    <input id='enqueueExts' name='exts' value='.mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp' />
    <div style='height:8px'></div>
    <label>Frame rate (video)</label>
    <input id='enqueueFps' type='number' step='0.1' value='1' name='fps' />
    <div style='height:8px'></div>
    <label>ML URL</label>
    <input id='enqueueMl' name='ml_url' value='http://127.0.0.1:8010' />
    <div style='height:8px'></div>
    <label>Species URL</label>
    <input id='enqueueSpecies' name='species_url' value='http://127.0.0.1:8100' />
    <div style='height:10px'></div>
    <button class='btn' type='button' id='btnEnqueuePreview' onclick='previewEnqueueFolder()'>Preview and Queue Files</button>
  </form>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Media / Source List</h3>
  <p class='job-meta' style='margin:0 0 10px 0'>Aggregates <b>all</b> image + video jobs in the database by source file (not limited to the recent jobs panel). {summary_table_page_size} sources per page.</p>
  <div class='actions' style='margin-bottom:10px'>{''.join(summary_pagination_bits)}</div>
  <div class='table-scroll'>
    <table class='tbl'>
      <thead>
        <tr><th>Source</th><th>Overall</th><th>Queued</th><th>Running</th><th>Done</th><th>Error</th><th>Cancelled</th><th>Frame Progress</th></tr>
      </thead>
      <tbody id='summaryBody'>
        {''.join(summary_rows) if summary_rows else '<tr><td colspan="8">No sources yet</td></tr>'}
      </tbody>
    </table>
  </div>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Media Frame Browser</h3>
  <p class='job-meta' style='margin:0 0 10px 0'>Which frames appear here follows <b>Settings → Hide blank frames</b> (same as Frame Results).</p>
  <div class='browser-row'>
    <div>
      <label>Sources</label>
      <div id='videoList' class='video-list'></div>
    </div>
    <div>
      <label>Frames (selected video)</label>
      <div id='frameList' class='frame-list'></div>
    </div>
    <div>
      <label>Inline Preview</label>
      <div id='inlinePreview' class='inline-preview'>
        <div class='job-meta'>Select a frame to preview</div>
      </div>
    </div>
  </div>
</div>
<div class='tabs'>
  <button id='tabResultsBtn' class='tab-btn active' type='button' onclick='showTab("results")'>Frame Results</button>
  <button id='tabRunsBtn' class='tab-btn' type='button' onclick='showTab("runs")'>Runs</button>
  <button id='tabSettingsBtn' class='tab-btn' type='button' onclick='showTab("settings")'>Settings</button>
</div>
<div id='tabResults' style='display:block'>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Frame Results (Searchable)</h3>
    <p class='job-meta' style='margin:0 0 8px 0'>Up to <b>{frame_results_page_size}</b> rows per page. Blank visibility: <b>Settings</b> tab.</p>
    <div class='actions' style='margin:0 0 8px 0'>
      <button class='btn btn-subtle btn-compact' type='button' onclick='previewExcelExport()'>Export Excel (Videos + Frames + Species)</button>
      <label for='exportPreviewRows' class='job-meta' style='display:inline-flex;align-items:center;gap:6px;margin:0'>
        Preview rows
        <select id='exportPreviewRows' style='padding:4px 6px;border:1px solid #cbd5e1;border-radius:6px;background:#fff'>
          <option value='5' selected>5</option>
          <option value='10'>10</option>
          <option value='20'>20</option>
        </select>
      </label>
      <span class='job-meta'>Includes default species short/type tags and manual tags.</span>
    </div>
    <label>Search by species, video, frame, or description</label>
    <input id='resultsSearch' placeholder='e.g. hedgehog, IMG_0406, frame_0003, blank' oninput='filterResults()' />
    <div style='height:8px'></div>
    <div class='actions' style='align-items:center'>
      <div id='resultsTagFilters' class='tag-list'></div>
      <span id='resultsTagFilterCount' class='job-meta'>0 tags active</span>
      <button class='btn btn-subtle btn-compact' type='button' onclick='clearTagFilters()'>Clear tag filters</button>
    </div>
    <div style='height:8px'></div>
    <div class='actions'>{''.join(pagination_bits)}</div>
    <div style='height:10px'></div>
    <div id='resultsBody' class='results-list'>
      {''.join(result_rows) if result_rows else '<div class="job-meta">No processed frames yet</div>'}
    </div>
  </div>
</div>
<div id='tabRuns' style='display:none'>
  <h3>Runs</h3>
  <div class='jobs'>{''.join(job_items)}</div>
</div>
<div id='tabSettings' style='display:none'>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Data Retention</h3>
    <p class='job-meta'>Manage retained outputs and job history. Cleanup only removes completed run folders under the configured output path. Active queued/running job output folders are preserved.</p>
    <div class='actions'>
      <a class='btn btn-subtle js-action' href='/cleanup-output' data-confirm='Delete completed run output folders under:
{output_label}

Active queued/running job folders will be preserved.'>Cleanup Output Folder</a>
      <a class='btn btn-subtle js-action' href='/reset-generated-media' data-confirm='Delete generated/local media files:
- input
- video
- run_* outputs

SQL job history will be kept.
Active job folders will be preserved.'>Reset Generated Media</a>
      <a class='btn btn-subtle js-action' href='/clear-jobs' data-confirm='Clear all job records from the SQL database?

This action cannot be undone.'>Clear SQL Job History</a>
      <a class='btn btn-subtle js-action' href='/reset-all' data-confirm='Reset everything?

This will:
- cancel active jobs
- clear SQL job history
- delete generated/local media and output files'>Reset All (Media + SQL History)</a>
    </div>
  </div>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Runtime Paths</h3>
    <p class='job-meta'>Use these folders for queued media and generated outputs. Changes apply to new work; existing jobs keep their saved output path.</p>
    <label>Input folder (images + extracted frames)</label>
    <input id='settingsInputDir' value='{input_label}' />
    <div style='height:8px'></div>
    <label>Video folder (uploaded videos)</label>
    <input id='settingsVideoDir' value='{video_label}' />
    <div style='height:8px'></div>
    <label>Output folder (run_* results)</label>
    <input id='settingsOutputDir' value='{output_label}' />
    <div style='height:10px'></div>
    <div class='actions'>
      <button class='btn' type='button' onclick='saveRuntimeSettings()'>Save Paths</button>
    </div>
    <p id='settingsPathMsg' class='job-meta' style='margin-top:10px'></p>
  </div>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Display</h3>
    <p class='job-meta'>One setting for both <b>Frame Results</b> (list + pagination) and <b>Video Frame Browser</b> (frame list + inline preview). Blanks are frames with no species match (label ending in <code>Blank</code> or containing <code>__Blank</code>).</p>
    <label style='display:block;margin-bottom:6px'>Species label mode</label>
    <select id='settingsSpeciesMode' onchange='applySpeciesModeSetting()' style='width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box'>
      <option value='short' {'selected' if species_mode == 'short' else ''}>Short species label</option>
      <option value='latin' {'selected' if species_mode == 'latin' else ''}>Latin name (Genus species)</option>
      <option value='full' {'selected' if species_mode == 'full' else ''}>Full taxonomy path</option>
    </select>
    <p class='job-meta' style='margin-top:8px'>This updates species text in Frame Results and Video Frame Browser. Frame cards still show a dedicated Latin line when available.</p>
    <div style='height:10px'></div>
    <label style='font-size:14px;display:flex;align-items:flex-start;gap:10px;cursor:pointer;max-width:52rem'>
      <input type='checkbox' id='settingsHideBlanks' style='width:auto;margin-top:4px' {'checked' if hide_blanks else ''} onchange='applyHideBlanksSetting()' />
      <span><b>Hide blank / no-match frames</b> — when checked, those frames are omitted from Frame Results and from the Video Frame Browser lists.</span>
    </label>
    <p class='job-meta' style='margin-top:14px'>Changing this reloads the page (frame results reset to page 1; summary page is kept).</p>
  </div>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Detection Thresholds</h3>
    <p class='job-meta'>Tune detector <b>filtering</b> (not model prediction values). These settings apply to newly processed jobs.</p>
    <label>Minimum confidence threshold to draw a box (0.0 - 1.0)</label>
    <input id='settingsDetMinConf' type='number' min='0' max='1' step='0.05' value='{detector_min_confidence:.2f}' />
    <p class='job-meta' style='margin-top:6px'>0.00 means show all detections. Higher values hide lower-confidence boxes.</p>
    <div style='height:10px'></div>
    <label style='font-size:14px;display:flex;align-items:flex-start;gap:10px;cursor:pointer;max-width:52rem'>
      <input type='checkbox' id='settingsSuppressBlankBoxes' style='width:auto;margin-top:4px' {'checked' if suppress_blank_species_boxes else ''} onchange='applySuppressBlankBoxesSetting()' />
      <span><b>Suppress boxes when species prediction is blank</b> (detector confidence is still stored in output JSON)</span>
    </label>
    <div style='height:10px'></div>
    <div class='actions'>
      <button class='btn' type='button' onclick='saveDetectionSettings()'>Save Detection Settings</button>
    </div>
    <p id='settingsDetectionMsg' class='job-meta' style='margin-top:10px'></p>
  </div>
</div>
<div id='appConfirmModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='appConfirmBackdrop'></div>
  <div class='app-modal-box'>
    <div id='appConfirmTitle' class='app-modal-title'>Please confirm</div>
    <div id='appConfirmBody' class='app-modal-body'></div>
    <div class='app-modal-actions'>
      <button type='button' class='btn btn-subtle' id='appConfirmCancel'>Cancel</button>
      <button type='button' class='btn' id='appConfirmOk'>OK</button>
    </div>
  </div>
</div>
<div id='enqueuePreviewModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='enqueuePreviewBackdrop'></div>
  <div class='app-modal-box' style='max-width:min(860px,96vw)'>
    <div class='app-modal-title'>Review media to queue</div>
    <div id='enqueuePreviewSummary' class='job-meta'></div>
    <div id='enqueuePreviewList'></div>
    <div class='app-modal-actions'>
      <button type='button' class='btn btn-subtle' id='enqueuePreviewCancel'>Cancel</button>
      <button type='button' class='btn' id='enqueuePreviewOk'>Queue Selected Files</button>
    </div>
  </div>
</div>
<div id='exportPreviewModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='exportPreviewBackdrop'></div>
  <div class='app-modal-box export-preview-box'>
    <div class='app-modal-title'>Excel Export Preview</div>
    <div id='exportPreviewSummary' class='app-modal-body'></div>
    <div id='exportPreviewTableWrap' class='preview-scroll'></div>
    <div id='exportPreviewStatus' class='job-meta' aria-live='polite'></div>
    <div class='app-modal-actions' style='margin-top:10px'>
      <button type='button' class='btn btn-subtle' id='exportPreviewCancel'>Cancel</button>
      <button type='button' class='btn' id='exportPreviewDownload'>Download Excel</button>
    </div>
  </div>
</div>
<div id='tagEditModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='tagEditBackdrop'></div>
  <div class='app-modal-box' style='max-width:min(560px,94vw)'>
    <div class='app-modal-title'>Edit manual tag</div>
    <div class='app-modal-body'>
      <div class='job-meta' id='tagEditFrameLabel' style='margin-bottom:8px'></div>
      <label>Manual tag</label>
      <input id='tagEditInput' placeholder='e.g. deer crossing trail' />
      <p class='job-meta' style='margin-top:8px'>Use commas for multiple tags (example: <code>trail-cam, night, fox</code>). Leave empty and save to clear tags.</p>
    </div>
    <div class='app-modal-actions'>
      <button type='button' class='btn btn-subtle' id='tagEditCancel'>Cancel</button>
      <button type='button' class='btn btn-subtle' id='tagEditUseSpecies'>Use Species Short Name</button>
      <button type='button' class='btn btn-subtle' id='tagEditClear'>Clear</button>
      <button type='button' class='btn' id='tagEditSave'>Save</button>
    </div>
  </div>
</div>
<div id='viewerOverlay' class='viewer-overlay'>
  <div class='viewer-box'>
    <div class='viewer-top'>
      <div id='viewerTitle'>Image Viewer</div>
      <button class='btn btn-subtle' type='button' onclick='closeViewer()'>Close</button>
    </div>
    <div class='viewer-controls'>
      <span>Zoom</span>
      <input id='zoomRange' type='range' min='20' max='400' value='100' oninput='setViewerZoom(this.value)' />
      <span id='zoomLabel'>100%</span>
      <button class='btn btn-subtle' type='button' onclick='setViewerZoom(100)'>Reset</button>
    </div>
    <div class='viewer-canvas'>
      <img id='viewerImage' class='viewer-img' src='' alt='preview' />
    </div>
  </div>
</div>
<script>
const SCROLL_KEY = 'wildlife_ui_scroll_y';
const TAB_KEY = 'wildlife_ui_active_tab';
const HAS_ACTIVE = {"true" if has_active else "false"};
let FRAME_RECORDS = {records_json};
const HIDE_BLANKS = {"true" if hide_blanks else "false"};
const SPECIES_MODE = {species_mode!r};
let CURRENT_ZOOM = 100;
let ACTIVE_VIDEO = '';
let ACTIVE_FRAME = '';
let _confirmResolve = null;
let _enqueuePreviewState = null;
let _exportPreviewHideBlanks = true;
let _exportDownloadInFlight = false;
let _lastBatchFolderPrompt = '';
let _tagEditRel = '';
let _tagEditSpeciesShort = '';
const ACTIVE_TAG_FILTERS = new Set();
function lastTaxonSegment(species) {{
  const parts = String(species || '').split(';').map((p) => p.trim());
  for (let i = parts.length - 1; i >= 0; i--) {{
    if (parts[i]) return parts[i];
  }}
  return '';
}}
function latinSpeciesName(species) {{
  const parts = String(species || '').split(';').map((p) => p.trim()).filter((p) => !!p);
  if (parts.length >= 2) return `${{parts[parts.length - 2].replace(/_/g, ' ')}} ${{parts[parts.length - 1].replace(/_/g, ' ')}}`;
  if (parts.length === 1) return parts[0].replace(/_/g, ' ');
  return '';
}}
function isBlankRecord(r) {{
  const s = String(r.species || '');
  const d = String(r.description || '').toLowerCase();
  if (d.includes('__blank')) return true;
  if (s.toLowerCase().includes('__blank')) return true;
  return lastTaxonSegment(s).toLowerCase() === 'blank';
}}
function formatSpeciesLabel(r) {{
  if (!r) return '—';
  if (isBlankRecord(r)) return 'No species match (blank)';
  const shortName = String(r.species_short || lastTaxonSegment(r.species || '') || '').trim();
  const latinName = String(r.species_latin || latinSpeciesName(r.species || '') || '').trim();
  const fullName = String(r.species || '').trim();
  if (SPECIES_MODE === 'latin') return latinName || shortName || fullName || '—';
  if (SPECIES_MODE === 'full') return fullName || shortName || '—';
  return shortName || fullName || '—';
}}
function fullTaxonomyLabel(r) {{
  return String((r && r.species) || '').trim();
}}
function trailcamOverlayLabel(r) {{
  if (!r) return '';
  const d = String(r.overlay_date || '').trim();
  const t = String(r.overlay_time || '').trim();
  const tempRaw = String(r.overlay_temp || '').trim();
  let temp = tempRaw;
  const m = tempRaw.match(/^(-?\\d+)\\s*([CF])$/i);
  if (m) temp = `${{m[1]}}\u00b0${{String(m[2]).toUpperCase()}}`;
  const bits = [];
  if (d) bits.push(d);
  if (t) bits.push(t);
  if (temp) bits.push(temp);
  return bits.join(' | ');
}}
function formatTrailTemp(tempRaw) {{
  const t = String(tempRaw || '').trim();
  if (!t) return '';
  const cleaned = t.replace('°', '');
  const m = cleaned.match(/^(-?\\d+)\\s*([CF])?$/i);
  if (m) return String(m[1] || '').trim();
  return cleaned;
}}
function frameNumberLabel(frameName) {{
  const s = String(frameName || '').trim();
  const m = s.match(/_frame_(\\d+)/i);
  if (!m) return '';
  const n = String(m[1] || '').replace(/^0+/, '') || '0';
  return `#${{n}}`;
}}
function openConfirmModal(message, opts = {{}}) {{
  return new Promise((resolve) => {{
    _confirmResolve = resolve;
    const title = document.getElementById('appConfirmTitle');
    const b = document.getElementById('appConfirmBody');
    if (title) title.textContent = String(opts.title || 'Please Confirm');
    if (b) {{
      if (opts.html) b.innerHTML = String(opts.html);
      else b.textContent = String(message || '');
    }}
    document.getElementById('appConfirmModal')?.classList.add('show');
  }});
}}
function closeConfirmModal(ok) {{
  document.getElementById('appConfirmModal')?.classList.remove('show');
  if (_confirmResolve) {{
    _confirmResolve(!!ok);
    _confirmResolve = null;
  }}
}}
function closeEnqueuePreview() {{
  document.getElementById('enqueuePreviewModal')?.classList.remove('show');
  _enqueuePreviewState = null;
}}
function closeExportPreview() {{
  if (_exportDownloadInFlight) return;
  document.getElementById('exportPreviewModal')?.classList.remove('show');
  const status = document.getElementById('exportPreviewStatus');
  if (status) status.textContent = '';
  const btn = document.getElementById('exportPreviewDownload');
  if (btn) {{
    btn.disabled = false;
    btn.textContent = 'Download Excel';
  }}
}}
function openTagEditModal(rel, currentTag, labelText, speciesShort) {{
  _tagEditRel = rel;
  _tagEditSpeciesShort = String(speciesShort || '').trim();
  const modal = document.getElementById('tagEditModal');
  const inp = document.getElementById('tagEditInput');
  const lbl = document.getElementById('tagEditFrameLabel');
  if (inp) inp.value = currentTag || '';
  if (lbl) lbl.textContent = labelText || rel || '';
  modal?.classList.add('show');
  setTimeout(() => inp?.focus(), 0);
}}
function closeTagEditModal() {{
  document.getElementById('tagEditModal')?.classList.remove('show');
  _tagEditRel = '';
  _tagEditSpeciesShort = '';
}}
function normalizeTagsCsv(raw) {{
  const parts = String(raw || '')
    .split(',')
    .map((x) => x.trim())
    .filter((x) => !!x);
  const seen = new Set();
  const out = [];
  for (const t of parts) {{
    const k = t.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(t);
  }}
  return out.join(', ');
}}
async function saveTagEditValue(nextValue) {{
  const rel = String(_tagEditRel || '').trim();
  if (!rel) return;
  const normalized = normalizeTagsCsv(nextValue);
  const res = await fetch('/api/frame-tag', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ annotated_rel: rel, tag_text: normalized }}),
  }});
  const data = await res.json();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Unable to save manual tags. Please try again.', {{ title: 'Save Manual Tags' }});
    return;
  }}
  closeTagEditModal();
  window.location.reload();
}}
async function rerunFrame(inputPath, jobId) {{
  const p = String(inputPath || '').trim();
  if (!p) {{
    await openConfirmModal('Cannot re-run this frame because input path is missing.', {{ title: 'Re-run Frame' }});
    return;
  }}
  const ok = await openConfirmModal(
    'Re-run this frame as a new image job?',
    {{ title: 'Re-run Frame' }}
  );
  if (!ok) return;
  const res = await fetch('/api/rerun-frame', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ input_path: p, job_id: Number(jobId || 0) || null }}),
  }});
  const data = await res.json();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Unable to re-run this frame.', {{ title: 'Re-run Frame' }});
    return;
  }}
  const msg = data.job_id ? `Re-ran frame in job #${{data.job_id}}.` : 'Frame re-run complete.';
  await openConfirmModal(msg, {{ title: 'Re-run Frame' }});
  showTab('runs');
  startRunsPolling();
  void refreshRunsJobs();
}}
function esc(s) {{
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');
}}
function formatConfidencePercent(raw) {{
  const s = String(raw || '').trim();
  if (!s) return '';
  const n = Number(s);
  if (!Number.isFinite(n)) return s;
  const pct = n <= 1 ? n * 100 : n;
  return `${{pct.toFixed(1)}}%`;
}}
async function previewEnqueueFolder() {{
  const folder = document.getElementById('enqueueFolderPath')?.value || '';
  if (!(await ensureBatchOutputChoice(folder))) return;
  const exts = document.getElementById('enqueueExts')?.value || '';
  const res = await fetch('/api/enqueue-folder-preview', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ folder_path: folder, exts: exts }}),
  }});
  const data = await res.json();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Unable to preview this folder. Please check the path and try again.', {{ title: 'Preview Folder' }});
    return;
  }}
  const items = data.items || [];
  if (items.length === 0) {{
    await openConfirmModal('No matching media files were found in the selected folder.\\n\\nCheck the folder path and extension filters, then try again.', {{ title: 'Preview Folder' }});
    return;
  }}
  const fps = document.getElementById('enqueueFps')?.value || '1';
  const ml = document.getElementById('enqueueMl')?.value || 'http://127.0.0.1:8010';
  const sp = document.getElementById('enqueueSpecies')?.value || 'http://127.0.0.1:8100';
  _enqueuePreviewState = {{ folder: folder, exts: exts, fps: fps, ml_url: ml, species_url: sp, items: items }};
  const done = items.filter((x) => x.prior_status === 'done').length;
  const active = items.filter((x) => x.prior_status === 'queued' || x.prior_status === 'running').length;
  const sum = document.getElementById('enqueuePreviewSummary');
  if (sum) sum.textContent = `${{items.length}} file(s) found — ${{done}} previously completed, ${{active}} already in queue.`;
  const list = document.getElementById('enqueuePreviewList');
  if (!list) return;
  list.innerHTML = '';
  list.style.maxHeight = '48vh';
  list.style.overflow = 'auto';
  items.forEach((it) => {{
    const st = it.prior_status;
    const row = document.createElement('div');
    row.className = 'enqueue-row' + ((st === 'queued' || st === 'running') ? ' disabled' : '');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.path = it.input_path;
    if (st === 'queued' || st === 'running') {{
      cb.disabled = true;
      cb.checked = false;
    }} else if (st === 'done') {{
      cb.disabled = false;
      cb.checked = false;
    }} else {{
      cb.disabled = false;
      cb.checked = true;
    }}
    const label = document.createElement('label');
    label.style.flex = '1';
    let note = 'new';
    if (st === 'done') note = `processed (job #${{it.prior_job_id || '?'}})`;
    else if (st === 'queued' || st === 'running') note = `in queue (job #${{it.prior_job_id || '?'}})`;
    else if (st) note = `last: ${{st}} (#${{it.prior_job_id || '?'}})`;
    label.innerHTML = `<b>${{esc(it.filename)}}</b> <span class='enqueue-meta'>(${{it.media_type}}) — ${{esc(note)}}</span>`;
    row.appendChild(cb);
    row.appendChild(label);
    list.appendChild(row);
  }});
  document.getElementById('enqueuePreviewModal')?.classList.add('show');
}}
async function commitEnqueuePreview() {{
  if (!_enqueuePreviewState) return;
  const paths = [];
  document.querySelectorAll('#enqueuePreviewList input[type=checkbox]').forEach((cb) => {{
    if (!cb.disabled && cb.checked && cb.dataset.path) paths.push(cb.dataset.path);
  }});
  if (paths.length === 0) {{
    await openConfirmModal('Select at least one file to queue.\\n\\nTip: completed files are unchecked by default, but you can enable them to re-run.', {{ title: 'Queue Selected Files' }});
    return;
  }}
  const body = {{
    folder_path: _enqueuePreviewState.folder,
    exts: _enqueuePreviewState.exts,
    fps: Number(_enqueuePreviewState.fps) || 1,
    ml_url: _enqueuePreviewState.ml_url,
    species_url: _enqueuePreviewState.species_url,
    input_paths: paths,
  }};
  const res = await fetch('/api/enqueue-folder-commit', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body),
  }});
  const data = await res.json();
  closeEnqueuePreview();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Unable to queue selected files. Please try again.', {{ title: 'Queue Selected Files' }});
    return;
  }}
  const u = new URL(window.location.origin + '/');
  u.searchParams.set('msg', data.message || 'Queued.');
  window.location.href = u.toString();
}}
(function initModals() {{
  document.getElementById('appConfirmOk')?.addEventListener('click', () => closeConfirmModal(true));
  document.getElementById('appConfirmCancel')?.addEventListener('click', () => closeConfirmModal(false));
  document.getElementById('appConfirmBackdrop')?.addEventListener('click', () => closeConfirmModal(false));
  document.getElementById('enqueuePreviewCancel')?.addEventListener('click', () => closeEnqueuePreview());
  document.getElementById('enqueuePreviewBackdrop')?.addEventListener('click', () => closeEnqueuePreview());
  document.getElementById('enqueuePreviewOk')?.addEventListener('click', async () => commitEnqueuePreview());
  document.getElementById('exportPreviewCancel')?.addEventListener('click', () => closeExportPreview());
  document.getElementById('exportPreviewBackdrop')?.addEventListener('click', () => closeExportPreview());
  document.getElementById('exportPreviewDownload')?.addEventListener('click', async () => {{
    if (_exportDownloadInFlight) return;
    const u = new URL('/export/frame-results.xlsx', window.location.origin);
    u.searchParams.set('hide_blanks', _exportPreviewHideBlanks ? '1' : '0');
    const btn = document.getElementById('exportPreviewDownload');
    const status = document.getElementById('exportPreviewStatus');
    _exportDownloadInFlight = true;
    if (btn) {{
      btn.disabled = true;
      btn.textContent = 'Preparing download...';
    }}
    if (status) status.textContent = 'Preparing Excel file. Download will start automatically.';
    try {{
      const resp = await fetch(u.toString(), {{ method: 'GET' }});
      if (!resp.ok) {{
        throw new Error(`HTTP ${{resp.status}}`);
      }}
      const blob = await resp.blob();
      const contentDisp = String(resp.headers.get('Content-Disposition') || '');
      let downloadName = 'frame-results.xlsx';
      const fileNameStar = contentDisp.match(/filename\*=UTF-8''([^;]+)/i);
      const fileNameBasic = contentDisp.match(/filename="?([^";]+)"?/i);
      if (fileNameStar && fileNameStar[1]) {{
        try {{
          downloadName = decodeURIComponent(String(fileNameStar[1]).trim());
        }} catch (_decodeErr) {{
          downloadName = String(fileNameStar[1]).trim();
        }}
      }} else if (fileNameBasic && fileNameBasic[1]) {{
        downloadName = String(fileNameBasic[1]).trim();
      }}
      const dl = document.createElement('a');
      dl.href = URL.createObjectURL(blob);
      dl.download = downloadName;
      document.body.appendChild(dl);
      dl.click();
      dl.remove();
      setTimeout(() => URL.revokeObjectURL(dl.href), 5000);
      if (status) status.textContent = 'Download started.';
      _exportDownloadInFlight = false;
      closeExportPreview();
    }} catch (_err) {{
      _exportDownloadInFlight = false;
      if (btn) {{
        btn.disabled = false;
        btn.textContent = 'Download Excel';
      }}
      if (status) status.textContent = '';
      await openConfirmModal('Download failed. Please try again.', {{ title: 'Excel Export' }});
    }}
  }});
  document.getElementById('tagEditCancel')?.addEventListener('click', () => closeTagEditModal());
  document.getElementById('tagEditBackdrop')?.addEventListener('click', () => closeTagEditModal());
  document.getElementById('tagEditClear')?.addEventListener('click', async () => saveTagEditValue(''));
  document.getElementById('tagEditUseSpecies')?.addEventListener('click', () => {{
    if (!_tagEditSpeciesShort) return;
    const inp = document.getElementById('tagEditInput');
    const current = inp?.value || '';
    const merged = normalizeTagsCsv((current ? `${{current}}, ` : '') + _tagEditSpeciesShort);
    if (inp) inp.value = merged;
  }});
  document.getElementById('tagEditSave')?.addEventListener('click', async () => {{
    const v = document.getElementById('tagEditInput')?.value || '';
    await saveTagEditValue(v);
  }});
  document.getElementById('tagEditInput')?.addEventListener('keydown', async (e) => {{
    if (e.key === 'Enter') {{
      e.preventDefault();
      const v = document.getElementById('tagEditInput')?.value || '';
      await saveTagEditValue(v);
    }} else if (e.key === 'Escape') {{
      e.preventDefault();
      closeTagEditModal();
    }}
  }});
}})();
window.addEventListener('beforeunload', () => {{
  sessionStorage.setItem(SCROLL_KEY, String(window.scrollY || 0));
}});
window.addEventListener('load', () => {{
  const u = new URL(window.location.href);
  if (u.searchParams.has('msg')) {{
    u.searchParams.delete('msg');
    window.history.replaceState(null, '', u.pathname + (u.search ? u.search : ''));
  }}
  const savedTab = sessionStorage.getItem(TAB_KEY);
  if (savedTab === 'runs' || savedTab === 'settings' || savedTab === 'results') {{
    showTab(savedTab);
  }}
  const y = Number(sessionStorage.getItem(SCROLL_KEY) || '0');
  if (Number.isFinite(y) && y > 0) {{
    window.scrollTo({{ top: y, behavior: 'auto' }});
  }}
}});
function bindJsActionLinks() {{
  document.querySelectorAll('a.js-action').forEach((el) => {{
    if (el.dataset.boundClick === '1') return;
    el.dataset.boundClick = '1';
    el.addEventListener('click', async (evt) => {{
    evt.preventDefault();
    const href = el.getAttribute('href');
    if (!href) return;
    const confirmMsg = el.getAttribute('data-confirm');
    if (confirmMsg) {{
      const ok = await openConfirmModal(confirmMsg);
      if (!ok) return;
    }}
    sessionStorage.setItem(SCROLL_KEY, String(window.scrollY || 0));
    try {{
      await fetch(href, {{ method: 'GET', credentials: 'same-origin' }});
    }} catch (_) {{
      // Fall back to regular navigation if request fails.
      window.location.href = href;
      return;
    }}
    window.location.reload();
    }});
  }});
}}
bindJsActionLinks();
function openViewer(src, title) {{
  const ov = document.getElementById('viewerOverlay');
  const img = document.getElementById('viewerImage');
  const ttl = document.getElementById('viewerTitle');
  if (!ov || !img || !ttl) return;
  img.src = src;
  ttl.textContent = title || 'Image Viewer';
  setViewerZoom(100);
  ov.style.display = 'flex';
}}
function closeViewer() {{
  const ov = document.getElementById('viewerOverlay');
  if (ov) ov.style.display = 'none';
}}
function setViewerZoom(v) {{
  const z = Math.max(20, Math.min(400, Number(v) || 100));
  CURRENT_ZOOM = z;
  const img = document.getElementById('viewerImage');
  const lbl = document.getElementById('zoomLabel');
  const rng = document.getElementById('zoomRange');
  if (img) img.style.transform = `scale(${{z / 100}})`;
  if (lbl) lbl.textContent = `${{z}}%`;
  if (rng && String(rng.value) !== String(z)) rng.value = String(z);
}}
function showTab(name) {{
  const next = (name === 'runs' || name === 'settings') ? name : 'results';
  sessionStorage.setItem(TAB_KEY, next);
  const isResults = next === 'results';
  const isRuns = next === 'runs';
  const isSettings = next === 'settings';
  document.getElementById('tabResults').style.display = isResults ? 'block' : 'none';
  document.getElementById('tabRuns').style.display = isRuns ? 'block' : 'none';
  const ts = document.getElementById('tabSettings');
  if (ts) ts.style.display = isSettings ? 'block' : 'none';
  document.getElementById('tabResultsBtn')?.classList.toggle('active', isResults);
  document.getElementById('tabRunsBtn')?.classList.toggle('active', isRuns);
  document.getElementById('tabSettingsBtn')?.classList.toggle('active', isSettings);
  if (isRuns) {{
    void refreshRunsJobs();
  }}
  if (isResults) {{
    void refreshResultsRecords();
  }}
  syncRunsPollingForVisibleTab();
  syncResultsPollingForVisibleTab();
}}
function applyHideBlanksSetting() {{
  const cb = document.getElementById('settingsHideBlanks');
  if (!cb) return;
  const u = new URL(window.location.href);
  u.searchParams.set('hide_blanks', cb.checked ? '1' : '0');
  u.searchParams.set('page', '1');
  window.location.href = u.toString();
}}
function applySpeciesModeSetting() {{
  const el = document.getElementById('settingsSpeciesMode');
  if (!el) return;
  const mode = String(el.value || 'short').toLowerCase();
  const chosen = (mode === 'latin' || mode === 'full') ? mode : 'short';
  const u = new URL(window.location.href);
  u.searchParams.set('species_mode', chosen);
  u.searchParams.set('page', '1');
  window.location.href = u.toString();
}}
async function previewExcelExport() {{
  const hideBlanks = HIDE_BLANKS;
  _exportPreviewHideBlanks = hideBlanks;
  const rows = hideBlanks
    ? FRAME_RECORDS.filter((r) => !isBlankRecord(r))
    : FRAME_RECORDS.slice();
  const step1 = await openConfirmModal(
    `Prepare export preview?\\n\\nRows available: ${{rows.length}}\\nHide blank frames: ${{hideBlanks ? 'ON' : 'OFF'}}`,
    {{ title: 'Export Frame Results' }}
  );
  if (!step1) return;
  const mode = hideBlanks ? 'Hide blank frames: ON' : 'Hide blank frames: OFF';
  const previewRowsEl = document.getElementById('exportPreviewRows');
  const parsedRows = Number(previewRowsEl?.value || '5');
  const previewCount = [5, 10, 20].includes(parsedRows) ? parsedRows : 5;
  const sample = rows.slice(0, previewCount);
  const tableRows = sample.map((r) => {{
    const jobId = esc(String(r.job_id || ''));
    const trailcamDate = esc(String(r.overlay_date || ''));
    const trailcamTime = esc(String(r.overlay_time || ''));
    const trailcamTemp = esc(formatTrailTemp(String(r.overlay_temp || '')));
    const species = esc(formatSpeciesLabel(r));
    const latinRaw = String(r.species_latin || latinSpeciesName(r.species || '') || '');
    const latin = esc(latinRaw);
    const speciesConfRaw = formatConfidencePercent(r.species_confidence || '');
    const speciesConf = esc(speciesConfRaw);
    const taxonomy = esc(String(r.species || ''));
    const shortTag = esc(String(r.species_short || ''));
    const typeTag = esc(String(r.species_type || ''));
    const manual = esc(String(r.manual_tag || ''));
    const speciesDisplayRaw = String(formatSpeciesLabel(r) || '');
    const descSpeciesRaw = [
      `Likely ${{speciesDisplayRaw}}`,
      latinRaw ? `(${{latinRaw}})` : '',
      speciesConfRaw ? `- confidence ${{speciesConfRaw}}` : '',
    ].filter((p) => !!p).join(' ');
    const descSpecies = esc(descSpeciesRaw);
    const detectorClass = String(r.detector_class || '').trim();
    const detectorConf = String(r.detector_confidence || '').trim();
    const descDetector = esc(detectorConf ? `${{detectorClass}} (${{detectorConf}})` : detectorClass);
    return `<tr><td>${{esc(String(r.source || ''))}}</td><td>${{esc(String(r.frame || ''))}}</td><td>${{trailcamDate}}</td><td>${{trailcamTime}}</td><td>${{trailcamTemp}}</td><td>${{species}}</td><td>${{latin}}</td><td>${{speciesConf}}</td><td>${{taxonomy}}</td><td>${{shortTag}}</td><td>${{typeTag}}</td><td>${{manual}}</td><td>${{descSpecies}}</td><td>${{descDetector}}</td><td>${{jobId}}</td></tr>`;
  }}).join('');
  const summary = document.getElementById('exportPreviewSummary');
  if (summary) {{
    summary.textContent = `Export rows: ${{rows.length}} (hide blanks: ${{hideBlanks ? 'ON' : 'OFF'}})\\nPreview: showing ${{sample.length}} of selected ${{previewCount}} row(s).`;
  }}
  const tableWrap = document.getElementById('exportPreviewTableWrap');
  if (tableWrap) {{
    tableWrap.innerHTML = `
      <table class="preview-table">
        <thead>
          <tr><th>Video</th><th>Frame</th><th>Trail Date</th><th>Trail Time</th><th>Trail Temp (°C)</th><th>Species</th><th>Latin</th><th>Species Conf (%)</th><th>Taxonomy</th><th>Default Short</th><th>Default Type</th><th>Manual Tag</th><th>Species Context</th><th>Detector Summary</th><th>Job</th></tr>
        </thead>
        <tbody>
          ${{tableRows || "<tr><td colspan='15'>No rows available with current filters.</td></tr>"}}
        </tbody>
      </table>
    `;
  }}
  document.getElementById('exportPreviewModal')?.classList.add('show');
}}
async function editManualTag(annotatedRel) {{
  const rel = String(annotatedRel || '').trim();
  if (!rel) return;
  const rec = FRAME_RECORDS.find((r) => String(r.annotated_rel || '') === rel);
  const current = String((rec && rec.manual_tag) || '');
  const label = rec ? `${{rec.source || ''}} :: ${{rec.frame || ''}}` : rel;
  let speciesShort = '';
  if (rec) {{
    speciesShort = isBlankRecord(rec) ? 'Blank' : (lastTaxonSegment(String(rec.species || '')) || String(rec.species || '').trim());
  }}
  openTagEditModal(rel, current, label, speciesShort);
}}
async function ensureBatchOutputChoice(folderPath) {{
  const folder = String(folderPath || '').trim();
  if (!folder) return true;
  const videoEl = document.getElementById('settingsVideoDir');
  const inputEl = document.getElementById('settingsInputDir');
  const outputEl = document.getElementById('settingsOutputDir');
  if (!videoEl || !inputEl || !outputEl) return true;
  const currentVideo = String(videoEl.value || '').trim();
  if (!currentVideo || folder.toLowerCase() === currentVideo.toLowerCase()) return true;
  if (_lastBatchFolderPrompt.toLowerCase() === folder.toLowerCase()) return true;

  const defaultOut = String(outputEl.value || '');
  const useDefault = window.confirm(
    `Batch folder changed:\\n${{folder}}\\n\\nWould you like to keep the current default output folder?\\n\\nOK = Keep default output\\n(${{defaultOut}})\\n\\nCancel = Choose a custom output folder now`
  );
  if (useDefault) {{
    _lastBatchFolderPrompt = folder;
    return true;
  }}

  const custom = window.prompt('Enter the output folder path to use for this batch folder:', outputEl.value || '');
  if (custom === null) return false;
  const customOut = String(custom || '').trim();
  if (!customOut) {{
    await openConfirmModal('Output folder cannot be empty.\\n\\nPlease enter a valid folder path.', {{ title: 'Set Output Folder' }});
    return false;
  }}

  const res = await fetch('/api/settings/runtime', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      input_dir: inputEl.value || '',
      video_dir: folder,
      output_dir: customOut,
    }}),
  }});
  const data = await res.json();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Unable to save the output folder. Please verify the path and try again.', {{ title: 'Set Output Folder' }});
    return false;
  }}
  videoEl.value = data.video_dir || folder;
  outputEl.value = data.output_dir || customOut;
  _lastBatchFolderPrompt = folder;
  const msg = document.getElementById('settingsPathMsg');
  if (msg) msg.textContent = `Saved runtime paths for batch folder: ${{folder}}`;
  return true;
}}
async function saveRuntimeSettings() {{
  const inputDir = document.getElementById('settingsInputDir')?.value || '';
  const videoDir = document.getElementById('settingsVideoDir')?.value || '';
  const outputDir = document.getElementById('settingsOutputDir')?.value || '';
  const msg = document.getElementById('settingsPathMsg');
  if (msg) msg.textContent = 'Saving...';
  try {{
    const res = await fetch('/api/settings/runtime', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        input_dir: inputDir,
        video_dir: videoDir,
        output_dir: outputDir,
      }}),
    }});
    const data = await res.json();
    if (!data.ok) {{
      if (msg) msg.textContent = data.error || 'Failed to save settings.';
      return;
    }}
    if (msg) msg.textContent = 'Saved. Reloading...';
    window.location.reload();
  }} catch (_) {{
    if (msg) msg.textContent = 'Failed to save settings.';
  }}
}}
async function saveDetectionSettings() {{
  const confRaw = document.getElementById('settingsDetMinConf')?.value || '0';
  const conf = Number(confRaw);
  const suppress = !!document.getElementById('settingsSuppressBlankBoxes')?.checked;
  const msg = document.getElementById('settingsDetectionMsg');
  if (!Number.isFinite(conf) || conf < 0 || conf > 1) {{
    if (msg) msg.textContent = 'Confidence must be between 0.0 and 1.0.';
    return;
  }}
  if (msg) msg.textContent = 'Saving...';
  try {{
    const res = await fetch('/api/settings/detection', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        detector_min_confidence: conf,
        suppress_blank_species_boxes: suppress,
      }}),
    }});
    const data = await res.json();
    if (!data.ok) {{
      if (msg) msg.textContent = data.error || 'Failed to save detection settings.';
      return;
    }}
    if (msg) msg.textContent = 'Saved. Applies to newly queued jobs.';
  }} catch (_) {{
    if (msg) msg.textContent = 'Failed to save detection settings.';
  }}
}}
function applySuppressBlankBoxesSetting() {{
  void saveDetectionSettings();
}}
function attachImageClickHandlers() {{
  document.querySelectorAll('#resultsBody img.thumb').forEach((img) => {{
    const src = img.getAttribute('src') || '';
    const card = img.closest('.result-card');
    const title = card ? (card.getAttribute('data-viewer-title') || 'Frame') : 'Frame';
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', (e) => {{
      e.preventDefault();
      openViewer(src, title);
    }});
    const a = img.closest('a');
    if (a) a.addEventListener('click', (e) => e.preventDefault());
  }});
}}
function renderInlinePreview(r) {{
  const box = document.getElementById('inlinePreview');
  if (!box) return;
  if (!r) {{
    box.innerHTML = "<div class='job-meta'>Select a frame to preview</div>";
    return;
  }}
  const src = `/files/${{r.annotated_rel}}`;
  const sp = esc(formatSpeciesLabel(r));
  const latin = esc(String(r.species_latin || latinSpeciesName(r.species || '') || ''));
  const taxonomyRaw = fullTaxonomyLabel(r);
  const taxonomy = esc(taxonomyRaw);
  const overlay = esc(trailcamOverlayLabel(r));
  const frameNo = frameNumberLabel(r.frame || '');
  const frameNoEsc = esc(frameNo);
  const zoomTitle = `${{String(r.frame || '')}} - ${{String(r.source || '')}}`;
  box.innerHTML = `
    <div><b>${{esc(r.frame)}}</b>${{frameNo ? ` <span class='job-meta'>(${{frameNoEsc}})</span>` : ''}}</div>
    <div class='job-meta'><b>Source:</b> ${{esc(r.source)}}</div>
    <div class='job-meta'><b>Species:</b> ${{sp}}</div>
    ${{latin && !isBlankRecord(r) ? `<div class='job-meta'><b>Latin:</b> ${{latin}}</div>` : ''}}
    ${{taxonomyRaw && !isBlankRecord(r) && taxonomyRaw !== formatSpeciesLabel(r) ? `<div class='job-meta'><b>Taxonomy:</b> ${{taxonomy}}</div>` : ''}}
    ${{overlay ? `<div class='job-meta'><b>Trail-cam stamp:</b> ${{overlay}}</div>` : ''}}
    <img src="${{src}}" alt="frame preview" loading="lazy" />
    <div class='job-meta'>${{esc(r.description || '')}}</div>
    <div><button class='btn btn-subtle' type='button' onclick="openViewer('${{src}}','${{zoomTitle.replace(/'/g, "\\\\'")}}')">Open Zoom Viewer</button></div>
  `;
}}
function renderVideoBrowser() {{
  const hideBlanks = HIDE_BLANKS;
  const sourceRecords = hideBlanks
    ? FRAME_RECORDS.filter((r) => !isBlankRecord(r))
    : FRAME_RECORDS.slice();
  const videoMap = new Map();
  for (const r of sourceRecords) {{
    const k = r.source || 'unknown';
    if (!videoMap.has(k)) videoMap.set(k, []);
    videoMap.get(k).push(r);
  }}
  const videoNames = Array.from(videoMap.keys())
    .filter((n) => (videoMap.get(n) || []).length > 0)
    .sort((a, b) => a.localeCompare(b));
  const vList = document.getElementById('videoList');
  const fList = document.getElementById('frameList');
  const prevBox = document.getElementById('inlinePreview');
  if (!vList || !fList) return;
  if (videoNames.length === 0) {{
    vList.innerHTML = "<div class='job-meta'>No sources with visible frames" + (hideBlanks ? " — <b>Settings</b>: turn off &quot;Hide blank…&quot; if every frame is blank." : "") + "</div>";
    fList.innerHTML = "<div class='job-meta'>No frames to show</div>";
    if (prevBox) prevBox.innerHTML = "<div class='job-meta'>No frame to preview</div>";
    return;
  }}
  if (!ACTIVE_VIDEO || !videoMap.has(ACTIVE_VIDEO)) ACTIVE_VIDEO = videoNames[0];
  vList.innerHTML = videoNames.map((name) => {{
    const active = name === ACTIVE_VIDEO ? ' active' : '';
    const cnt = videoMap.get(name).length;
    return `<button class='video-item${{active}}' type='button' data-video='${{esc(name)}}'>${{esc(name)}} (${{cnt}})</button>`;
  }}).join('');
  vList.querySelectorAll('.video-item').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      ACTIVE_VIDEO = btn.getAttribute('data-video') || '';
      ACTIVE_FRAME = '';
      renderVideoBrowser();
    }});
  }});
  const frames = (videoMap.get(ACTIVE_VIDEO) || []).slice().sort((a, b) => String(a.frame).localeCompare(String(b.frame)));
  if (!ACTIVE_FRAME && frames.length > 0) ACTIVE_FRAME = String(frames[0].annotated_rel);
  if (frames.length > 0 && !frames.some((x) => String(x.annotated_rel) === ACTIVE_FRAME)) {{
    ACTIVE_FRAME = String(frames[0].annotated_rel);
  }}
  fList.innerHTML = frames.map((r) => {{
    const src = `/files/${{r.annotated_rel}}`;
    const active = String(r.annotated_rel) === ACTIVE_FRAME ? ' active' : '';
    const speciesText = esc(formatSpeciesLabel(r));
    const taxonomyRaw = fullTaxonomyLabel(r);
    const taxonomyText = esc(taxonomyRaw);
    const overlay = esc(trailcamOverlayLabel(r));
    const taxonomyLine = (taxonomyRaw && !isBlankRecord(r) && taxonomyRaw !== formatSpeciesLabel(r))
      ? `<div class='job-meta'>Taxonomy: ${{taxonomyText}}</div>`
      : '';
    const overlayLine = overlay ? `<div class='job-meta'>Trail-cam: ${{overlay}}</div>` : '';
    return `<button class='frame-item${{active}}' type='button' data-src='${{src}}' data-id='${{esc(String(r.annotated_rel))}}' data-title='${{esc((r.source || '') + ' :: ' + (r.frame || ''))}}'><div>${{esc(String(r.frame))}} | ${{speciesText}}</div>${{taxonomyLine}}${{overlayLine}}</button>`;
  }}).join('');
  fList.querySelectorAll('.frame-item').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      ACTIVE_FRAME = btn.getAttribute('data-id') || '';
      const picked = frames.find((x) => String(x.annotated_rel) === ACTIVE_FRAME);
      renderInlinePreview(picked || null);
      renderVideoBrowser();
    }});
  }});
  const first = frames.find((x) => String(x.annotated_rel) === ACTIVE_FRAME) || frames[0];
  renderInlinePreview(first || null);
}}
async function queueSelectedFiles() {{
  const picker = document.getElementById('multiFiles');
  const files = (window._droppedFiles && window._droppedFiles.length) ? window._droppedFiles : (picker?.files || []);
  if (!files || files.length === 0) {{
    alert('Select files (or a folder) first.');
    return;
  }}
  const ml = document.querySelector("input[name='ml_url']")?.value || 'http://127.0.0.1:8010';
  const sp = document.querySelector("input[name='species_url']")?.value || 'http://127.0.0.1:8100';
  const fps = document.querySelector("input[name='fps']")?.value || '1';
  const fd = new FormData();
  for (const f of files) fd.append('media_files', f);
  fd.append('ml_url', ml);
  fd.append('species_url', sp);
  fd.append('fps', fps);
  const res = await fetch('/process-multi', {{ method: 'POST', body: fd }});
  if (res.redirected) {{
    window.location.href = res.url;
    return;
  }}
  window.location.reload();
}}
const dz = document.getElementById('dropZone');
if (dz) {{
  dz.addEventListener('dragover', (e) => {{
    e.preventDefault();
    dz.style.borderColor = '#3b82f6';
  }});
  dz.addEventListener('dragleave', () => {{
    dz.style.borderColor = '#94a3b8';
  }});
  dz.addEventListener('drop', (e) => {{
    e.preventDefault();
    dz.style.borderColor = '#94a3b8';
    const files = Array.from(e.dataTransfer?.files || []);
    window._droppedFiles = files;
    dz.textContent = files.length ? `${{files.length}} file(s) ready` : 'Drag & drop files here';
  }});
}}
document.getElementById('viewerOverlay')?.addEventListener('click', (e) => {{
  if (e.target && e.target.id === 'viewerOverlay') closeViewer();
}});
function filterResults(){{
  const q = (document.getElementById('resultsSearch')?.value || '').toLowerCase().trim();
  const rows = document.querySelectorAll('.result-row');
  rows.forEach((row)=>{{
    const text = (row.getAttribute('data-search') || '').toLowerCase();
    const tagsCsv = (row.getAttribute('data-tags') || '').toLowerCase();
    const rowTags = new Set(tagsCsv.split(',').map((x) => x.trim()).filter((x) => !!x));
    const matchQ = !q || text.includes(q);
    let matchTags = true;
    if (ACTIVE_TAG_FILTERS.size > 0) {{
      for (const t of ACTIVE_TAG_FILTERS) {{
        if (!rowTags.has(t)) {{
          matchTags = false;
          break;
        }}
      }}
    }}
    row.style.display = (matchQ && matchTags) ? '' : 'none';
  }});
}}
function clearTagFilters() {{
  ACTIVE_TAG_FILTERS.clear();
  renderTagFilterChips();
  filterResults();
}}
function renderTagFilterChips() {{
  const box = document.getElementById('resultsTagFilters');
  const counter = document.getElementById('resultsTagFilterCount');
  if (!box) return;
  const map = new Map();
  FRAME_RECORDS.forEach((r) => {{
    const bits = []
      .concat(String(r.species_short || '').split(','))
      .concat(String(r.species_type || '').split(','))
      .concat(String(r.manual_tag || '').split(','))
      .map((x) => x.trim())
      .filter((x) => !!x);
    bits.forEach((t) => {{
      const key = t.toLowerCase();
      if (!map.has(key)) map.set(key, t);
    }});
  }});
  const tags = Array.from(map.keys()).sort((a, b) => a.localeCompare(b));
  if (tags.length === 0) {{
    box.innerHTML = "<span class='job-meta'>No tags yet</span>";
    if (counter) counter.textContent = '0 tags active';
    return;
  }}
  box.innerHTML = tags.map((k) => {{
    const label = esc(map.get(k) || k);
    const active = ACTIVE_TAG_FILTERS.has(k) ? ' active' : '';
    return `<button class='tag-chip tag-chip-filter${{active}}' type='button' data-tag='${{esc(k)}}'>${{label}}</button>`;
  }}).join('');
  box.querySelectorAll('.tag-chip-filter').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      const k = String(btn.getAttribute('data-tag') || '').toLowerCase().trim();
      if (!k) return;
      if (ACTIVE_TAG_FILTERS.has(k)) ACTIVE_TAG_FILTERS.delete(k);
      else ACTIVE_TAG_FILTERS.add(k);
      renderTagFilterChips();
      filterResults();
    }});
  }});
  if (counter) {{
    const n = ACTIVE_TAG_FILTERS.size;
    counter.textContent = `${{n}} tag${{n === 1 ? '' : 's'}} active`;
  }}
}}
function renderResultsBodyFromRecords() {{
  const body = document.getElementById('resultsBody');
  if (!body) return;
  if (!Array.isArray(FRAME_RECORDS) || FRAME_RECORDS.length === 0) {{
    body.innerHTML = "<div class='job-meta'>No processed frames yet</div>";
    return;
  }}
  body.innerHTML = FRAME_RECORDS.map((r) => {{
    const speciesShort = String(r.species_short || '');
    const speciesType = String(r.species_type || '');
    const manualTag = String(r.manual_tag || '');
    const manualBits = manualTag.split(',').map((x) => x.trim()).filter((x) => !!x);
    const defaultBits = [speciesShort, speciesType].filter((x) => !!x);
    const allBits = [];
    const seen = new Set();
    defaultBits.concat(manualBits).forEach((t) => {{
      const k = String(t || '').toLowerCase();
      if (!k || seen.has(k)) return;
      seen.add(k);
      allBits.push(t);
    }});
    const tagsNorm = allBits.map((t) => String(t || '').toLowerCase()).join(',');
    const speciesDisp = formatSpeciesLabel(r);
    const latin = String(r.species_latin || latinSpeciesName(r.species || '') || '');
    const taxonomyRaw = fullTaxonomyLabel(r);
    const taxonomy = esc(taxonomyRaw);
    const overlay = esc(trailcamOverlayLabel(r));
    const isBlank = isBlankRecord(r);
    const searchBlob = (
      String(r.source || '') + ' '
      + String(r.frame || '') + ' '
      + String(r.species || '') + ' '
      + String(speciesDisp || '') + ' '
      + String(r.description || '') + ' '
      + String(manualTag || '') + ' '
      + String(speciesShort || '') + ' '
      + String(latin || '') + ' '
      + String(speciesType || '') + ' blank no species match'
    ).toLowerCase();
    const defaultHtml = defaultBits.length
      ? `<div><b>Default tags:</b></div><div class='tag-list'>${{defaultBits.map((t) => `<span class='tag-chip default'>${{esc(t)}}</span>`).join('')}}</div>`
      : '';
    const manualHtml = manualBits.length
      ? `<div><b>Manual tags:</b></div><div class='tag-list'>${{manualBits.map((t) => `<span class='tag-chip'>${{esc(t)}}</span>`).join('')}}</div>`
      : '';
    const latinHtml = (latin && !isBlank) ? `<div><b>Latin:</b> ${{esc(latin)}}</div>` : '';
    const taxonomyHtml = (taxonomyRaw && !isBlank && taxonomyRaw !== speciesDisp) ? `<div><b>Taxonomy:</b> ${{taxonomy}}</div>` : '';
    const overlayHtml = overlay ? `<div><b>Trail-cam stamp:</b> ${{overlay}}</div>` : '';
    const rel = String(r.annotated_rel || '');
    const relJs = JSON.stringify(rel);
    const inputJs = JSON.stringify(String(r.input_abs || ''));
    const jobJs = JSON.stringify(String(r.job_id || ''));
    return (
      `<div class='result-card result-row' data-viewer-title='${{esc(String(r.source || ''))}} - ${{esc(String(r.frame || ''))}}' data-is-blank='${{isBlank ? '1' : '0'}}' data-tags='${{esc(tagsNorm)}}' data-search='${{esc(searchBlob)}}'>`
      + `<div><a href='/files/${{rel}}' target='_blank'><img src='/files/${{rel}}' class='thumb' onerror="this.onerror=null;this.replaceWith(document.createTextNode('Image removed'))"/></a></div>`
      + "<div class='result-text'>"
      + `<div><b>Job:</b> #${{esc(String(r.job_id || ''))}}</div>`
      + `<div><b>Video:</b> ${{esc(String(r.source || ''))}}</div>`
      + `<div><b>Frame:</b> ${{esc(String(r.frame || ''))}}</div>`
      + `<div><b>Species:</b> ${{esc(String(speciesDisp || '—'))}}</div>`
      + latinHtml + taxonomyHtml + overlayHtml + defaultHtml + manualHtml
      + `<div class='desc-col' title='${{esc(String(r.description || ''))}}'>${{esc(String(r.description || ''))}}</div>`
      + `<div style='margin-top:4px' class='actions'><button class='btn btn-subtle' type='button' onclick='editManualTag(${{relJs}})'>Edit tag</button><button class='btn btn-subtle' type='button' onclick='rerunFrame(${{inputJs}}, ${{jobJs}})'>Re-run frame</button></div>`
      + "</div></div>"
    );
  }}).join('');
  attachImageClickHandlers();
}}
function statusClassFor(st) {{
  if (st === 'queued') return 'st-queued';
  if (st === 'running') return 'st-running';
  if (st === 'done') return 'st-done';
  if (st === 'error') return 'st-error';
  if (st === 'cancelled') return 'st-cancelled';
  return '';
}}
function renderJobProgressHtml(done, total) {{
  const t = Number(total || 0);
  const d = Number(done || 0);
  if (!Number.isFinite(t) || t <= 0) return '';
  const safeDone = Math.max(0, Math.min(d, t));
  const pct = Math.max(0, Math.min(100, Math.round((safeDone / t) * 100)));
  return `<div class='progress'><div class='bar' style='width:${{pct}}%'></div></div><div class='job-meta'>Progress: ${{safeDone}}/${{t}}</div>`;
}}
function renderLiveJobActions(job) {{
  const id = Number(job.id || 0);
  if (!id) return '';
  let html = '';
  if (job.can_pause) {{
    html += `<a class='btn btn-subtle btn-compact js-action' href='/pause-job/${{id}}' data-confirm='Pause this run now?\\n\\nYou can continue it later from this Runs card.'>Pause</a> `;
  }}
  if (job.can_reprocess) {{
    html += `<a class='btn btn-subtle btn-compact js-action' href='/retry/${{id}}' data-confirm='Reprocess this completed run from the beginning?\\n\\nA fresh output set will be generated.'>Reprocess</a> `;
  }}
  if (job.can_continue) {{
    html += `<a class='btn btn-subtle btn-compact js-action' href='/continue-job/${{id}}' data-confirm='Continue this run?\\n\\nThis resumes from saved progress in the existing run folder.'>Continue</a> `;
  }}
  if (job.can_cancel) {{
    html += `<a class='btn btn-subtle btn-compact js-action' href='/cancel/${{id}}' data-confirm='Cancel this queued job?'>Cancel</a>`;
  }}
  if (job.has_out_links) {{
    html += ` <a class='btn btn-subtle btn-compact' href='/browse-output/${{id}}'>Output Browser</a>`;
    html += ` <a class='btn btn-subtle btn-compact' href='/open-output/${{id}}'>Open Folder</a>`;
  }}
  return html;
}}
function overallForSummary(c) {{
  if ((c.running || 0) > 0) return 'running';
  if ((c.done || 0) > 0) return 'done';
  if ((c.queued || 0) > 0) return 'queued';
  if ((c.error || 0) > 0) return 'error';
  return 'cancelled';
}}
function refreshSummaryFromLiveJobs(jobs) {{
  const body = document.getElementById('summaryBody');
  if (!body) return;
  const agg = new Map();
  jobs.forEach((j) => {{
    const source = String(j.source || '').trim();
    if (!source) return;
    if (!agg.has(source)) {{
      agg.set(source, {{
        queued: 0,
        running: 0,
        done: 0,
        error: 0,
        cancelled: 0,
        total: 0,
        processed: 0,
      }});
    }}
    const a = agg.get(source);
    const st = String(j.status || '');
    if (Object.prototype.hasOwnProperty.call(a, st)) a[st] += 1;
    a.total += Number(j.total_items || 0);
    a.processed += Number(j.processed_items || 0);
  }});
  body.querySelectorAll('tr').forEach((tr) => {{
    const tds = tr.querySelectorAll('td');
    if (tds.length < 8) return;
    const source = String(tds[0]?.textContent || '').trim();
    const a = agg.get(source);
    if (!a) return;
    const pct = a.total > 0 ? Math.round((a.processed / a.total) * 100) : 0;
    tds[1].textContent = overallForSummary(a);
    tds[2].textContent = String(a.queued);
    tds[3].textContent = String(a.running);
    tds[4].textContent = String(a.done);
    tds[5].textContent = String(a.error);
    tds[6].textContent = String(a.cancelled);
    tds[7].textContent = `${{a.processed}}/${{a.total}} (${{pct}}%)`;
  }});
}}
let _jobsPollTimer = null;
let _resultsPollTimer = null;
const _jobStateCache = new Map();
async function refreshRunsJobs() {{
  try {{
    const res = await fetch('/api/jobs-live?limit=200', {{ cache: 'no-store' }});
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.ok || !Array.isArray(data.jobs)) return;
    let hasActive = false;
    let shouldSyncFrames = false;
    data.jobs.forEach((job) => {{
      const id = Number(job.id || 0);
      if (!id) return;
      const status = String(job.status || '');
      const processed = Number(job.processed_items || 0);
      const total = Number(job.total_items || 0);
      const prior = _jobStateCache.get(id);
      if (!prior || prior.status !== status || prior.processed !== processed || prior.total !== total) {{
        shouldSyncFrames = true;
        _jobStateCache.set(id, {{ status, processed, total }});
      }}
      if (status === 'queued' || status === 'running') hasActive = true;
      const stEl = document.getElementById(`jobStatus_${{id}}`);
      if (stEl) {{
        stEl.textContent = status || '-';
        stEl.className = `status ${{statusClassFor(status)}}`;
      }}
      const metaEl = document.getElementById(`jobMeta_${{id}}`);
      if (metaEl) {{
        const created = String(job.created_at || '');
        const started = String(job.started_at || '') || '-';
        const finished = String(job.finished_at || '') || '-';
        metaEl.textContent = `Created: ${{created}} | Started: ${{started}} | Finished: ${{finished}}`;
      }}
      const cfgEl = document.getElementById(`jobCfg_${{id}}`);
      if (cfgEl) {{
        const conf = Number(job.detector_min_confidence || 0);
        const confText = Number.isFinite(conf) ? conf.toFixed(2) : '0.00';
        const suppress = !!job.suppress_blank_species_boxes;
        cfgEl.textContent = `Detection: conf ≥ ${{confText}} | suppress blank boxes: ${{suppress ? 'on' : 'off'}}`;
      }}
      const progEl = document.getElementById(`jobProg_${{id}}`);
      if (progEl) progEl.innerHTML = renderJobProgressHtml(job.processed_items, job.total_items);
      const logEl = document.getElementById(`jobLog_${{id}}`);
      if (logEl) logEl.textContent = String(job.last_log || '-');
      const errEl = document.getElementById(`jobErr_${{id}}`);
      if (errEl) {{
        const msg = String(job.error_text || '').trim();
        errEl.textContent = msg;
        errEl.style.display = msg ? '' : 'none';
      }}
      const actEl = document.getElementById(`jobActions_${{id}}`);
      if (actEl) {{
        actEl.innerHTML = renderLiveJobActions(job);
      }}
      const prevEl = document.getElementById(`jobPreview_${{id}}`);
      if (prevEl) {{
        const rel = String(job.preview_rel || '');
        if (rel) {{
          prevEl.innerHTML = `<img src='/files/${{rel}}' class='preview' onerror="this.onerror=null;this.replaceWith(document.createTextNode('Preview not available (file removed)'))"/>`;
        }} else {{
          prevEl.innerHTML = '';
        }}
      }}
    }});
    bindJsActionLinks();
    refreshSummaryFromLiveJobs(data.jobs);
    if (shouldSyncFrames) {{
      void refreshResultsRecords();
    }}
    if (!hasActive && _jobsPollTimer) {{
      clearInterval(_jobsPollTimer);
      _jobsPollTimer = null;
    }}
  }} catch (_) {{
    // Ignore transient poll errors and retry on next interval.
  }}
}}
function startRunsPolling() {{
  if (_jobsPollTimer) return;
  _jobsPollTimer = setInterval(() => {{
    void refreshRunsJobs();
  }}, 2000);
  void refreshRunsJobs();
}}
function stopRunsPolling() {{
  if (!_jobsPollTimer) return;
  clearInterval(_jobsPollTimer);
  _jobsPollTimer = null;
}}
function syncRunsPollingForVisibleTab() {{
  if (!HAS_ACTIVE) {{
    stopRunsPolling();
    return;
  }}
  const runsTab = document.getElementById('tabRuns');
  const isRunsVisible = !!runsTab && runsTab.style.display === 'block';
  if (isRunsVisible) startRunsPolling();
  else stopRunsPolling();
}}
async function refreshResultsRecords() {{
  try {{
    const hide = HIDE_BLANKS ? '1' : '0';
    const res = await fetch(`/api/frame-records-live?hide_blanks=${{hide}}&limit=500`, {{ cache: 'no-store' }});
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.ok || !Array.isArray(data.records)) return;
    FRAME_RECORDS = data.records;
    renderResultsBodyFromRecords();
    renderTagFilterChips();
    filterResults();
    renderVideoBrowser();
    if (!data.has_active) {{
      stopResultsPolling();
    }}
  }} catch (_) {{
    // Ignore transient polling errors.
  }}
}}
function startResultsPolling() {{
  if (_resultsPollTimer) return;
  _resultsPollTimer = setInterval(() => {{
    void refreshResultsRecords();
  }}, 2500);
  void refreshResultsRecords();
}}
function stopResultsPolling() {{
  if (!_resultsPollTimer) return;
  clearInterval(_resultsPollTimer);
  _resultsPollTimer = null;
}}
function syncResultsPollingForVisibleTab() {{
  if (!HAS_ACTIVE) {{
    stopResultsPolling();
    return;
  }}
  const resultsTab = document.getElementById('tabResults');
  const isResultsVisible = !!resultsTab && resultsTab.style.display === 'block';
  if (isResultsVisible) startResultsPolling();
  else stopResultsPolling();
}}
function manualSyncNow() {{
  void refreshRunsJobs();
  void refreshResultsRecords();
}}
attachImageClickHandlers();
renderTagFilterChips();
renderVideoBrowser();
syncRunsPollingForVisibleTab();
syncResultsPollingForVisibleTab();
</script>
</div></body></html>"""

