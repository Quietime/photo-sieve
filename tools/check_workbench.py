"""Real-window feature and interaction matrix; no network or original face work in navigation."""
import hashlib,json,sys,tempfile,time,shutil
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtCore import Qt,QSettings,QPoint,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import Library
from desktop import Window,STYLE
from workflow_data import WorkRecords
from compare_view import CompareDialog
from tools.check_ratings import FakeEngine
from xmp_sync import inspect,sync_out

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE);checks=[];errors=[];rating_timings=[]
    def until(fn,timeout=40):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            app.processEvents()
            if fn():return
            QTest.qWait(10)
        raise AssertionError('Workbench condition timed out')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);folder=base/'演唱会';folder.mkdir()
        for i in range(32):
            im=Image.new('RGB',(900,600) if i%4<2 else (600,900),('navy','red','green','orange')[i//8]);ImageDraw.Draw(im).rectangle((50,80,200,250),fill='white');exif=Image.Exif();exif[36867]=f'2024:06:29 15:{i//8:02}:{i%8:02}';im.save(folder/f'{i:03}.jpg',exif=exif)
        library=Library(base/'workspace');library.scan(folder);until(lambda:not library.job['running'])
        for p in library.list_photos('all',limit=64):library.preview_image(p['id'],1600).close()
        # Keep one photo only in the 3200 bucket, like a migrated high-DPI cache.
        last=dict(library.photo(library.list_photos('all',limit=64)[-1]['id']));old=library.cache_path(last['path'],last['size'],last['mtime'],1600);high=library.cache_path(last['path'],last['size'],last['mtime'],3200);old.replace(high)
        library.cache_path(last['path'],last['size'],last['mtime'],320).unlink(missing_ok=True)
        with library.connect() as db:
            db.execute('DELETE FROM cache_index WHERE photo_id=?',(last['id'],));db.execute('INSERT INTO cache_index VALUES(?,?,?,?,?)',(high.name,last['id'],last['size'],last['mtime'],3200))
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('face_preview',False);settings.setValue('preload_count',16);settings.setValue('rating/segment_gap',30)
        with patch('desktop.QSettings',return_value=settings),patch('rating_page.QSettings',return_value=settings),patch('face_service.QSettings',return_value=settings),patch('update_ui.QSettings',return_value=settings):
            w=Window(library,check_updates=False,data_root=base/'workspace');w.error=errors.append;w.resize(1600,1100);w.show();dialog=None
            try:
                until(lambda:w.keep_button.isEnabled());page=w.rating_page;page.engine.close();page.engine=FakeEngine();w.toggle_rating();until(lambda:not page.listing and not page.loading and page.pane.pixmap is not None);tools=page.tools;records=tools.records;ids=list(page.ids);first=ids[0]
                with patch.object(library,'decode',side_effect=AssertionError('Analysis must not decode originals')):
                    w.analysis.start(library,force=True);until(lambda:w.analysis.finished)
                assert records.summary()['features']==32
                checks.append('all_imported_non_deleted_images_analyzed_from_existing_cache_without_original_decode')
                page.auto.setChecked(False);page.select(0);tools.label('Blue');until(lambda:not w.pending_ops)
                assert records.annotations()[first]['label']=='Blue' and inspect(folder/'000.xmp')['label']=='Blue'
                assert page.grid.label_colors[first] and library.photo(first)['status']==0
                checks.append('color_label_persists_draws_and_syncs_xmp_without_changing_culling')
                page.setFocus();QTest.keyClick(w,Qt.Key.Key_1,Qt.KeyboardModifier.AltModifier);until(lambda:not w.pending_ops)
                assert records.annotations()[first]['label']=='Red'
                tools.open();tools.dialog.undo();until(lambda:not w.pending_ops and not page.listing)
                assert records.annotations()[first]['label']=='Blue' and inspect(folder/'000.xmp')['label']=='Blue'
                tools.label('Red');until(lambda:not w.pending_ops);tools.dialog.close()
                tools.label_filter.setCurrentIndex(tools.label_filter.findData('Red'));until(lambda:not page.listing)
                assert page.ids==[first]
                tools.label_filter.setCurrentIndex(0);until(lambda:not page.listing)
                assert len(page.ids)==32
                checks.append('label_shortcuts_and_filters_are_independent_of_star_keys')
                page.select(page.ids.index(first));page.grade(4);until(lambda:not page.writing and not w.pending_ops)
                assert inspect(folder/'000.xmp')['stars']==4 and inspect(folder/'000.xmp')['label']=='Red'
                page.undo();until(lambda:not page.writing and not w.pending_ops)
                assert inspect(folder/'000.xmp')['stars']==0
                checks.append('native_rating_auto_xmp_and_rating_undo_keep_sidecar_consistent')
                tools.mode.setCurrentIndex(1);until(lambda:not page.listing)
                assert tools.groups and sum(len(g['ids']) for g in tools.groups)==32
                chosen=next(g for g in tools.groups if len(g['ids'])>1);tools.group.setCurrentIndex(tools.group.findData(chosen['key']));until(lambda:not page.listing)
                assert set(page.ids)==set(chosen['ids']);w.grab().save(str(output/'similar-group-filter.png'))
                checks.append('similar_groups_and_group_filter_cover_inventory_without_moving_files')
                tools.group.setCurrentIndex(0);until(lambda:not page.listing);tools.open();dialog=tools.dialog
                dialog.auto.setChecked(False);assert not tools.auto_xmp and settings.value('rating/auto_xmp',True,type=bool)==False
                dialog.auto.setChecked(True);assert tools.auto_xmp
                dialog.refresh();until(lambda:not page.listing)
                assert settings.value('rating/segment_gap',0,type=int)==30
                until(lambda:'未筛选' in dialog.summary.text());dialog.grab().save(str(output/'progress-dashboard.png'))
                dialog.group_mode.setCurrentIndex(2);until(lambda:not page.listing)
                assert len(tools.groups)==4
                selection=dialog.table.selectionModel();selection.select(dialog.table.model().index(0,0),selection.SelectionFlag.Select|selection.SelectionFlag.Rows)
                dialog.segment.setText('第一首歌');dialog.name_groups();until(lambda:not w.pending_ops and not page.listing)
                assert any(a['segment']=='第一首歌' for a in records.annotations().values())
                dialog.split();until(lambda:not w.pending_ops and not page.listing)
                assert records.annotations()[page.pid()]['split_before']==1
                checks.append('time_segments_song_names_and_manual_split_persist')
                # Merge two rows through actual table selections and undo without culling history.
                selection=dialog.table.selectionModel();selection.select(dialog.table.model().index(0,0),selection.SelectionFlag.Select|selection.SelectionFlag.Rows);selection.select(dialog.table.model().index(1,0),selection.SelectionFlag.Select|selection.SelectionFlag.Rows)
                dialog.merge();until(lambda:not w.pending_ops and not page.listing)
                assert any(a['merge_key'] for a in records.annotations().values())
                dialog.undo();until(lambda:not w.pending_ops and not page.listing)
                assert library.state()['counts']['keep']==0
                checks.append('manual_group_merge_and_annotation_undo_do_not_touch_culling_history')
                dialog.tabs.setCurrentIndex(1);app.processEvents();dialog.grab().save(str(output/'group-tools.png'));dialog.close()
                tools.mode.setCurrentIndex(0);until(lambda:not page.listing and not page.loading);page.select(0)
                compare=CompareDialog(page);compare.show();until(lambda:not compare.loading)
                assert len(compare.visible_ids)==2 and all(p.pixmap for p in compare.panes[:2]);compare.count.setCurrentIndex(1);until(lambda:not compare.loading)
                assert len(compare.visible_ids)==4 and all(p.pixmap for p in compare.panes)
                compare.activate(1);target=compare.visible_ids[1];before={pid:page.info.get(pid,{}).get('stars') for pid in compare.visible_ids};compare.grade(5)
                assert page.pid()==target and page.info[target]['stars']==5 and page.info[target]['pending']
                until(lambda:not page.writing and not w.pending_ops)
                assert all(page.info.get(pid,{}).get('stars')==star for pid,star in before.items() if pid!=target)
                checks.append('two_and_four_way_compare_rate_only_highlighted_target_without_auto_advance')
                with patch.object(library,'decode',side_effect=AssertionError('Compare fit must use disk cache')):
                    compare.load();until(lambda:not compare.loading)
                compare.activate(0);driver=compare.panes[0];compare.zoom(0,driver.viewport().rect().center());until(lambda:driver.zoom_animation is None);compare.sync()
                ratios=[p.transform().m11()/p.fit_scale() for p in compare.panes];assert max(ratios)-min(ratios)<.02,ratios
                driver.centerOn(driver.sceneRect().width()*.6,driver.sceneRect().height()*.6);compare.sync();until(lambda:len(compare.overlays)==4 and not compare.detail_active and not compare.detail_wanted)
                assert all(item.pixmap().width()<=2048 and item.pixmap().height()<=2048 for item in compare.overlays.values())
                compare.grab().save(str(output/'four-way-linked-compare.png'))
                compare.fit_all();assert not compare.overlays
                until(lambda:all(p.zoom_animation is None for p in compare.panes));compare.close();app.processEvents();assert not compare.alive
                checks.append('comparison_cache_reuse_linked_zoom_pan_restore_and_close')
                # An externally changed sidecar must not revert the verified native rating.
                page.select(page.ids.index(first));page.grade(3);until(lambda:not w.pending_ops and not page.writing);path=folder/'000.xmp';path.write_bytes(path.read_bytes().replace(b'Rating="3"',b'Rating="1"'));external=path.read_bytes()
                page.grade(4);until(lambda:not w.pending_ops and not page.writing)
                assert page.info[first]['stars']==4 and path.read_bytes()==external and first in tools.xmp_errors
                checks.append('external_xmp_conflict_does_not_rollback_verified_native_rating_or_overwrite_external_edit')
                errors.clear();tools.open();dialog=tools.dialog;dialog.tabs.setCurrentIndex(2);dialog.import_current();until(lambda:not w.pending_ops and not page.listing)
                actual=page.engine.run(library.photo(first)['path'])
                assert actual['stars']==1 and first not in tools.xmp_errors,dict(native=actual['stars'],photo_id=first,current=page.pid(),xmp_errors=tools.xmp_errors,errors=errors)
                checks.append('explicit_xmp_import_resolves_conflict_via_verified_native_rating')
                dialog.export_all();until(lambda:not getattr(dialog,'bulk_running',False),60)
                assert all((folder/f'{i:03}.xmp').is_file() for i in range(32))
                checks.append('bulk_xmp_yields_in_small_chunks_and_reports_each_failure')
                dialog.close();tools.mode.setCurrentIndex(0);until(lambda:not page.listing);saved_labels=records.annotations();old_count=len(page.ids);library.trash(first);page.reload();until(lambda:not page.listing)
                assert first not in page.ids;library.undo();page.reload();until(lambda:not page.listing)
                assert len(page.ids)==old_count and records.annotations()[first]['label']==saved_labels[first]['label']
                checks.append('trash_restore_rescan_preserve_labels_and_exclude_deleted_from_analysis_filters')
                w.analysis.clear();assert records.summary()['features']==0 and records.annotations()[first]['label']==saved_labels[first]['label'];w.analysis.start(library,force=True);until(lambda:w.analysis.finished)
                checks.append('clear_analysis_rebuilds_derived_data_but_keeps_annotations_and_stars')
                # 500 mixed-photo rating inputs with a bounded pending horizon.
                tools.auto_xmp=True;expected={};peak=0
                for turn in range(500):
                    until(lambda:page.pending_count<32)
                    index=turn%len(page.ids);page.select(index);stars=(turn//len(page.ids))%6;pid=page.pid()
                    start=time.perf_counter();page.grade(stars,advance=False);rating_timings.append((time.perf_counter()-start)*1000)
                    assert page.info[pid]['stars']==stars;expected[pid]=stars;peak=max(peak,page.pending_count)
                    if turn%8==0:app.processEvents()
                until(lambda:not page.writing and not w.pending_ops,120)
                assert all(page.engine.run(library.photo(pid)['path'])['stars']==stars and inspect(Path(library.photo(pid)['path']).with_suffix('.xmp'))['stars']==stars for pid,stars in expected.items())
                assert peak<=32 and records.annotations()[first]['label']==saved_labels[first]['label']
                checks.append('500_rapid_rating_inputs_keep_native_xmp_labels_and_pending_queue_consistent')
                # Native-sized face crop is analyzed only by the background worker, then shown cached.
                from face_service import SCHEMA,cache_key,cache_path
                photo=dict(library.photo(first));crop=Image.effect_noise((256,256),80).convert('RGB');payload_path=cache_path(library,photo);crop.save(payload_path)
                with library.connect() as db:
                    db.execute(SCHEMA);db.execute('INSERT OR REPLACE INTO face_metadata VALUES(?,?,?,?,?,?,?)',(cache_key(photo),photo['path'],photo['size'],photo['mtime'],'test','[{"box":[0,0,1,1]}]','{}'))
                w.analysis.refresh(first);until(lambda:not w.analysis.working and not w.analysis.revisit)
                with library.connect() as db:quality=json.loads(db.execute('SELECT quality FROM work_features WHERE photo_id=?',(first,)).fetchone()[0])
                assert quality.get('sharpness') is not None
                page.select(page.ids.index(first));page.images.pop(first,None);page.image_sizes.pop(first,None);page.show_current();until(lambda:not page.loading);assert page.images[first][0]['_work_quality']['sharpness'] is not None
                checks.append('face_quality_hints_are_background_cached_and_follow_current_photo')
                # Concurrent native rating and label edits while both filters are active.
                target=page.ids[-1];tools.label('Blue',[target]);until(lambda:not w.pending_ops)
                tools.label_filter.setCurrentIndex(tools.label_filter.findData('Blue'));until(lambda:not page.listing)
                tools.mode.setCurrentIndex(1);until(lambda:not page.listing)
                assert page.ids==[target];until(lambda:page.can_grade() and not page.listing);page.grade(5,advance=False);assert page.info[target]['stars']==5;tools.label('Green')
                until(lambda:not w.pending_ops and not page.writing and not page.listing)
                assert not page.ids and records.annotations()[target]['label']=='Green'
                assert inspect(Path(library.photo(target)['path']).with_suffix('.xmp'))['stars']==5
                tools.label_filter.setCurrentIndex(0);tools.mode.setCurrentIndex(0);until(lambda:not page.listing)
                assert len(page.ids)==32;page.select(page.ids.index(first));until(lambda:not page.loading)
                checks.append('concurrent_rating_and_label_edits_refresh_active_group_and_color_filters_without_stale_results')
                w.grab().save(str(output/'workbench-rating.png'))
                # The actual screening undo must rebuild analysis even with face preview disabled.
                w.faces.enabled=False;w.toggle_rating();until(lambda:w.delete_button.isEnabled());restored_pid=w.queue[0]['id'];w.delete_button.click();until(lambda:not w.pending_ops and w.counts.get('deleted')==1)
                with library.connect() as db:db.execute('DELETE FROM work_features WHERE photo_id=?',(restored_pid,))
                w.restore();until(lambda:not w.pending_ops and w.counts.get('deleted')==0)
                until(lambda:any(r['signature'] for r in records.inventory({restored_pid})),30)
                w.toggle_rating();until(lambda:not page.listing and not page.loading)
                checks.append('screening_restore_rebuilds_derived_analysis_when_face_preview_is_disabled')
                # Late callbacks after comparison closes or modes change must be ignored.
                compare=CompareDialog(page);compare.show();compare.close();w.toggle_rating();w.toggle_rating();until(lambda:not page.listing and not page.loading)
                assert not compare.alive and page.active
                tools.open();visible_dialog=tools.dialog;w.toggle_rating();assert not visible_dialog.isVisible()
                before=records.annotations()[first]['label'];tools.label('Purple');app.processEvents();assert records.annotations()[first]['label']==before
                w.toggle_rating();until(lambda:not page.listing and not page.loading)
                checks.append('mode_changes_and_late_comparison_results_do_not_mix_photo_identity')
                assert not errors,errors
            finally:
                if dialog:dialog.close()
                w.prompt_on_close=False;w.close();until(lambda:w.closed)
    report=dict(ok=True,checks=checks,rating_inputs=500,max_rating_handler_ms=max(rating_timings),p95_rating_handler_ms=sorted(rating_timings)[int(len(rating_timings)*.95)],peak_pending=peak);(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':run(sys.argv[1])
