"""Compact entry points for local grouping, labels, progress and XMP exchange."""
import json,secrets,time
from PySide6.QtCore import Qt,QTimer,QAbstractTableModel,QAbstractListModel,QModelIndex,QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTabWidget,QWidget,QCheckBox,QSpinBox,QLineEdit,QTableView,QAbstractItemView,QMenu,QMessageBox,QScrollArea,QHeaderView,QComboBox
from rating_combo import RoundedCombo
from widgets import CapsuleProgress
from workflow_data import WorkRecords,LABELS,COLORS
from xmp_sync import sync_out,sync_in
import theme

class GroupChoiceModel(QAbstractListModel):
    def __init__(self,groups,parent=None):super().__init__(parent);self.groups=groups;self.positions={g['key']:i+1 for i,g in enumerate(groups)}
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.groups)+1
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0<=index.row()<=len(self.groups):return None
        if index.row()==0:return '全部组' if role==Qt.ItemDataRole.DisplayRole else None
        group=self.groups[index.row()-1]
        if role==Qt.ItemDataRole.UserRole:return group['key']
        if role==Qt.ItemDataRole.DisplayRole:return f'{index.row()}. {group["title"]} · {len(group["ids"])} 张'
        if role==Qt.ItemDataRole.ToolTipRole:return group['title']

class GroupCombo(RoundedCombo):
    def __init__(self):
        super().__init__();self.choice_model=None;self.setMaximumWidth(340);self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.setMinimumContentsLength(12);self.view().setUniformItemSizes(True);self.set_groups([])
    def set_groups(self,groups):
        old=self.currentData();previous=self.choice_model;self.choice_model=GroupChoiceModel(groups,self);self.setModel(self.choice_model);self.setCurrentIndex(self.choice_model.positions.get(old,0))
        # QComboBox deletes its previous child model when setModel replaces it.
    def sizeHint(self):return QSize(max(180,min(280,self.fontMetrics().horizontalAdvance(self.currentText())+44)),38)
    def minimumSizeHint(self):return QSize(150,38)

class GroupModel(QAbstractTableModel):
    def __init__(self,groups):super().__init__();self.groups=groups
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.groups)
    def columnCount(self,parent=QModelIndex()):return 3
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        group=self.groups[index.row()]
        if role==Qt.ItemDataRole.DisplayRole:return (str(index.row()+1),group['title'],str(len(group['ids'])))[index.column()]
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if orientation==Qt.Orientation.Horizontal and role==Qt.ItemDataRole.DisplayRole:return ['分段','名称 / 起始时间','照片数'][section]

class Workbench:
    def __init__(self,page,layout,heading):
        self.page=page;self.host=page.host;self.records=None;self.annotations={};self.groups=[];self.group_map={};self.syncing=set();self.xmp_errors={};self.dialog=None;self.last_summary=0;self.summary_pending=False;self.filter_dirty=False;self.last_interaction=time.monotonic()
        self.auto_xmp=page.host.mode_settings('rating').value('auto_xmp',True,type=bool)
        self.button=self.host.button('选片工具',self.open);heading.insertWidget(heading.count()-1,self.button)
        row=QHBoxLayout();row.setSpacing(8)
        self.label_filter=RoundedCombo();self.label_filter.setMinimumWidth(125);self.label_filter.addItem('全部颜色',None)
        for key,text in LABELS.items():self.label_filter.addItem(text,key)
        self.mode=RoundedCombo();self.mode.setMinimumWidth(125)
        for text,key in [('不分组','none'),('相似连拍分组','similar'),('按时间分段','time')]:self.mode.addItem(text,key)
        self.group=GroupCombo();self.group.setMinimumWidth(150);self.group.hide()
        self.mark=self.host.button('颜色标签 ▾',self.label_menu);self.compare=self.host.button('多图对比',self.open_compare)
        for widget in (self.label_filter,self.mode,self.group,self.mark,self.compare):row.addWidget(widget)
        row.addStretch();layout.insertLayout(2,row)
        self.label_filter.currentIndexChanged.connect(page.reload);self.mode.currentIndexChanged.connect(self.mode_changed);self.group.currentIndexChanged.connect(page.reload)
        self.quality=QLabel();self.quality.setObjectName('caption');self.quality.setWordWrap(True)
        # The hint belongs to the selected photo, beside the existing face/metadata block.
        page.aux.parentWidget().layout().addWidget(self.quality);self.quality.setAlignment(Qt.AlignmentFlag.AlignCenter);self.quality.setMaximumHeight(40)
        self.progress=QLabel();self.progress.setObjectName('caption');self.progress.setToolTip('清晰预览、人脸和本地分析均在后台执行；缓存达到上限时会显示实际状态')
        layout.insertWidget(layout.count()-1,self.progress)
        self.timer=QTimer(page);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()
    def attach(self,library):
        if self.records and self.records.library is library:return
        self.records=WorkRecords(library);self.annotations={};self.quality_map={};self.groups=[];self.group_map={};self.xmp_errors.clear()
        with library.connect() as db:self.xmp_errors={r[0]:r[1] for r in db.execute("SELECT x.photo_id,x.error FROM work_xmp x JOIN photos p ON p.id=x.photo_id WHERE p.present=1 AND x.error!=''")}
        if not hasattr(library,'work_session_seq'):
            with library.connect() as db:library.work_session_seq=db.execute('SELECT coalesce(max(seq),0) FROM history').fetchone()[0]
        self.label_filter.blockSignals(True);self.label_filter.setCurrentIndex(0);self.label_filter.blockSignals(False)
        self.mode.blockSignals(True);self.mode.setCurrentIndex(0);self.mode.blockSignals(False)
        self.reset_groups()
        if self.dialog:self.dialog.summary.setText('正在读取当前文件夹…');self.dialog.sync_status.clear();self.dialog.bulk_cancelled=True
    def snapshot(self):return dict(label=self.label_filter.currentData(),mode=self.mode.currentData(),group=self.group.currentData(),gap=self.host.mode_settings('rating').value('segment_gap',90,type=int))
    def filter_ids(self,ids,options,records=None):
        records=records or self.records;annotations=records.annotations();wanted=set(ids)
        if options['label'] is not None:wanted={pid for pid in wanted if annotations.get(pid,{}).get('label','')==options['label']}
        groups=records.groups(options['mode'],options['gap'],wanted) if options['mode']!='none' else []
        if options['group']:
            chosen=next((g for g in groups if g['key']==options['group']),None)
            if chosen:wanted.intersection_update(chosen['ids'])
        if options['mode']!='none':ordered=[pid for g in groups for pid in g['ids'] if pid in wanted]
        else:ordered=[pid for pid in ids if pid in wanted]
        return ordered,groups,annotations
    def reloaded(self,groups,annotations):
        self.annotations=annotations;self.page.grid.label_colors={pid:COLORS.get(a['label']) for pid,a in annotations.items() if a['label']};self.groups=groups;self.group_map={pid:g for g in groups for pid in g['ids']}
        self.group.blockSignals(True);self.group.set_groups(groups);self.group.blockSignals(False);self.group.setVisible(self.mode.currentData()!='none');self.show_quality()
        if self.dialog:
            self.dialog.set_groups(groups);self.dialog.group_mode.blockSignals(True);self.dialog.group_mode.setCurrentIndex(self.mode.currentIndex());self.dialog.group_mode.blockSignals(False)
    def reset_groups(self):
        self.group.blockSignals(True);self.group.set_groups([]);self.group.blockSignals(False)
    def refresh_filters(self):
        self.filter_dirty=True
        if not self.page.writing:self.filter_dirty=False;self.page.reload()
    def mode_changed(self):self.last_interaction=time.monotonic();self.reset_groups();self.refresh_filters()
    def label_menu(self):
        menu=QMenu(self.page)
        for index,(key,name) in enumerate(LABELS.items()):
            action=menu.addAction(name+('  Alt+'+str(index) if index<6 else ''));action.triggered.connect(lambda _,key=key:self.label(key))
        menu.exec(self.mark.mapToGlobal(self.mark.rect().bottomLeft()))
    def label(self,key,ids=None):
        p=self.page
        if not p.active or not self.records or self.records.library is not self.host.library or p.host.closing or p.host.busy or p.listing:return
        ids=list(ids or ([p.pid()] if p.pid() else []))
        if not ids:return
        records=self.records
        def done(_):
            if records is not self.records:return
            for pid in ids:self.annotations.setdefault(pid,dict(photo_id=pid,label='',segment='',split_before=0,merge_key=''))['label']=key
            p.grid.label_colors={pid:COLORS.get(a['label']) for pid,a in self.annotations.items() if a['label']};p.grid.viewport().update()
            self.host.toast.show_message('颜色标签已保存：'+LABELS[key],'info',1500)
            if self.auto_xmp:
                for pid in ids:self.export_one(pid,label=key)
            if self.label_filter.currentData() is not None:
                self.filter_dirty=True
                if not p.writing:self.filter_dirty=False;p.reload()
        self.host.op(lambda:records.annotate(ids,label=key),done,self.host.error)
    def show_quality(self):
        p=self.page;pid=p.pid()
        if not self.records or not pid:self.quality.clear();return
        # This is an in-memory copy populated by background reads, never face inference here.
        photo=p.images.get(pid,(p.thumb_photos.get(pid,{}),))[0]
        cached=getattr(self,'quality_map',{}).get(pid);embedded=photo.get('_work_quality')
        hint=cached if cached and cached.get('state')!='pending' else embedded or cached
        self.quality.setText(hint.get('text','') if hint else '人脸质量后台分析中' if self.host.faces.enabled else '人脸预览已关闭')
        self.quality.setToolTip((hint or {}).get('notice','只作选片参考，不会自动删除或评级'))
    def export_one(self,pid,stars=None,label=None,force=False,complete=None):
        if not self.records or self.records.library is not self.host.library or self.host.closing:return
        records=self.records;library=records.library;self.syncing.add(pid)
        def done(result):
            self.syncing.discard(pid)
            if records is self.records:self.xmp_errors.pop(pid,None)
            if complete:complete(True,result)
        def failed(message):
            self.syncing.discard(pid)
            if records is self.records:self.xmp_errors[pid]=message;self.host.error('XMP 未同步（原图评级已保留）：'+str(pid)+' · '+message)
            if complete:complete(False,message)
        self.host.op(lambda:sync_out(records,pid,stars,label,force),done,failed)
    def after_rating(self,result):
        if self.auto_xmp:self.export_one(result['photo_id'],stars=result['stars'])
    def tick(self):
        if self.host.closed:return
        p=self.page
        if not p.active:return
        cache=self.host.full_cache.status();faces=self.host.full_faces.status();analysis=self.host.analysis.status()
        def count(s):return f'{s.get("visited",s.get("done",0)):,}/{s.get("total") or 0:,}'
        self.progress.setText('后台缓存 · 预览 '+count(cache)+' · 人脸 '+(count(faces) if self.host.faces.enabled else '已关闭')+' · 分析 '+count(analysis)+(f' · XMP {len(self.xmp_errors)} 项待处理' if self.xmp_errors else ''))
        token=(id(self.records),self.host.analysis.generation,analysis['done'])
        if time.monotonic()-self.last_interaction>1 and not (self.dialog and self.dialog.isVisible()) and analysis['finished'] and getattr(self,'last_analysis_done',None)!=token and self.mode.currentData()!='none' and not p.listing and not p.writing:
            self.last_analysis_done=token;self.refresh_filters()
        self.progress.setVisible(bool(self.host.session_active));self.button.setEnabled(not self.host.closing and not self.host.busy)
        for widget in (self.label_filter,self.mode,self.group):widget.setEnabled(not p.writing and not p.listing and not self.host.closing and not self.host.busy)
        self.mark.setEnabled(bool(p.pid()) and not p.listing and not self.host.closing);self.compare.setEnabled(bool(p.pid()) and not p.listing and not self.host.closing)
        if not self.summary_pending and self.records and time.monotonic()-self.last_summary>=2:
            self.summary_pending=True;records=self.records;ids=p.grid.visible_ids();current=p.pid()
            def work():
                result=records.summary();quality={}
                with records.library.connect() as db:
                    part=list(dict.fromkeys([*ids,current]));part=[pid for pid in part if pid]
                    if part:
                        for row in db.execute('SELECT f.photo_id,f.quality FROM work_features f JOIN photos p ON p.id=f.photo_id AND p.size=f.size AND p.mtime=f.mtime WHERE p.present=1 AND f.photo_id IN ('+','.join('?' for _ in part)+')',part):quality[row[0]]=json.loads(row[1])
                return result,quality
            def done(result):
                self.summary_pending=False;self.last_summary=time.monotonic()
                if records is not self.records:return
                summary,quality=result;self.quality_map=quality;self.show_quality()
                if self.dialog and self.dialog.isVisible():self.dialog.update_summary(summary)
            self.host.submit(work,done,lambda _:setattr(self,'summary_pending',False),priority=-15)
        if self.dialog and self.dialog.isVisible():self.dialog.update_progress(cache,faces,analysis)
    def open(self):
        if not self.page.active or not self.records or self.records.library is not self.host.library or self.host.closing:return
        if self.dialog:self.dialog.show();self.dialog.raise_();self.last_summary=0;self.tick();return
        self.dialog=WorkbenchDialog(self);self.dialog.show();self.last_summary=0;self.tick()
    def open_compare(self):
        if not self.page.pid():return
        from compare_view import CompareDialog
        dialog=CompareDialog(self.page);self.compare_dialog=dialog
        try:dialog.exec()
        finally:self.compare_dialog=None
    def hide(self):
        if self.dialog:self.dialog.bulk_cancelled=True;self.dialog.close()
        compare=getattr(self,'compare_dialog',None)
        if compare and compare.alive:compare.close()
    def shutdown(self):self.timer.stop();self.hide()

class WorkbenchDialog(QDialog):
    def __init__(self,tools):
        super().__init__(tools.page);self.tools=tools;self.setWindowTitle('选片工具');self.resize(800,650);self.setMinimumSize(680,520)
        self.setStyleSheet('QTabWidget::pane {background:'+theme.BG+';border:1px solid '+theme.SEPARATOR+';border-radius:10px;} QTabBar::tab {background:'+theme.ELEVATED+';color:'+theme.SECONDARY+';padding:10px 16px;margin:0 3px 5px 0;border-radius:7px;} QTabBar::tab:selected {background:'+theme.BLUE+';color:'+theme.LABEL+';} QTableView {background:'+theme.BG+';color:'+theme.LABEL+';gridline-color:'+theme.SEPARATOR+';border:0;} QHeaderView::section {background:'+theme.ELEVATED+';color:'+theme.SECONDARY+';padding:6px;border:0;} QLineEdit,QSpinBox {background:#3F434A;border:1px solid '+theme.SEPARATOR+';border-radius:6px;padding:6px;}')
        layout=QVBoxLayout(self);tabs=QTabWidget();self.tabs=tabs;layout.addWidget(tabs);self.summary=QLabel('正在读取统计…');self.summary.setWordWrap(True)
        stats=QWidget();s=QVBoxLayout(stats);s.addWidget(self.summary);self.bars={}
        for key,name in [('cache','清晰预览'),('face','人脸裁剪'),('analysis','分组与质量分析')]:
            label=QLabel(name);bar=CapsuleProgress();bar.setFixedHeight(10);s.addWidget(label);s.addWidget(bar);self.bars[key]=(label,bar)
        self.cache_note=QLabel();self.cache_note.setWordWrap(True);s.addWidget(self.cache_note);s.addStretch();tabs.addTab(stats,'进度概览')
        group=QWidget();g=QVBoxLayout(group);g.addWidget(QLabel('按拍摄时间排序后分组；缺失拍摄时间使用文件时间。相似分组不会移动原图。'))
        self.group_mode=RoundedCombo();self.group_mode.addItems(['不分组','相似连拍分组','按时间分段']);self.group_mode.setCurrentIndex(tools.mode.currentIndex());self.group_mode.currentIndexChanged.connect(tools.mode.setCurrentIndex);g.addWidget(self.group_mode)
        gaprow=QHBoxLayout();gaprow.addWidget(QLabel('时间分段间隔（秒）'));self.gap=QSpinBox();self.gap.setRange(1,7200);self.gap.setValue(tools.host.mode_settings('rating').value('segment_gap',90,type=int));gaprow.addWidget(self.gap)
        gaprow.addWidget(tools.host.button('重新分析 / 刷新分组',self.refresh));g.addLayout(gaprow)
        self.table=QTableView();self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.set_groups(tools.groups);g.addWidget(self.table)
        row=QHBoxLayout();self.segment=QLineEdit();self.segment.setPlaceholderText('歌曲 / 环节名称');self.segment.setMaxLength(120);row.addWidget(self.segment);row.addWidget(tools.host.button('命名所选分段',self.name_groups));g.addLayout(row)
        row=QHBoxLayout()
        for name,fn in [('在当前照片前拆分',self.split),('合并所选组',self.merge),('取消所选组手动合并',self.unmerge),('撤销分组 / 标签修改',self.undo)]:row.addWidget(tools.host.button(name,fn))
        g.addLayout(row);g.addWidget(QLabel('相似判断以画面和相邻拍摄间隔为依据，不会自动识别歌名；选择“按时间分段”可手动标注歌曲。'));tabs.addTab(group,'连拍与分段')
        xmp=QWidget();x=QVBoxLayout(xmp);self.auto=QCheckBox('评级或颜色标签改变后，自动同步标准 XMP');self.auto.setChecked(tools.auto_xmp);self.auto.toggled.connect(self.auto_changed);x.addWidget(self.auto)
        text=QLabel('ARW 等 RAW 使用同名 .xmp 边车，包含标准 Rating / Label；Lightroom 导入后可“从文件读取元数据”。已有关键词、后期调整会保留。颜色使用 Red / Yellow / Green / Blue / Purple 标签集；Lightroom 需使用同名标签集。\n\n外部文件已改变时停止覆盖。选择“从 XMP 读取”会把其星级写入原图，颜色写入本地；拒绝标记不会自动删除照片。');text.setWordWrap(True);x.addWidget(text)
        self.sync_status=QLabel();self.sync_status.setWordWrap(True);x.addWidget(self.sync_status)
        for name,fn in [('将当前照片写入 XMP',self.export_current),('从当前照片的 XMP 读取',self.import_current),('将当前筛选结果写入 XMP',self.export_all),('冲突处理：用应用内容覆盖当前 XMP',self.force_current)]:x.addWidget(tools.host.button(name,fn))
        x.addWidget(tools.host.button('停止批量同步',self.cancel_bulk));x.addStretch();tabs.addTab(xmp,'Lightroom / XMP')
        labels=QWidget();l=QVBoxLayout(labels)
        for i,(key,name) in enumerate(LABELS.items()):l.addWidget(tools.host.button(name+f' · Alt+{i}',lambda _,key=key:tools.label(key)))
        l.addWidget(QLabel('颜色标签与星级、保留/删除相互独立。可在分级页按颜色筛选。'));l.addStretch();tabs.addTab(labels,'颜色标签')
        close=tools.host.button('完成',self.close);layout.addWidget(close,0,Qt.AlignmentFlag.AlignRight)
    def update_summary(self,s):
        self.summary.setText(f'未筛选 {s["pending"]:,} 张 · 保留 {s["keep"]:,} 张 · 已删除 {s["deleted"]:,} 张\n本次筛选操作 {s["session_actions"]:,} 次 · 未读取评级 {s["unknown"]:,} 张\n'+'  '.join(f'{i}星 {s["ratings"].get(i,0):,}' for i in range(6)))
        self.sync_status.setText('\n'.join(f'照片 {pid}：{message}' for pid,message in list(self.tools.xmp_errors.items())[:8]) or '没有待处理的同步冲突。')
    def update_progress(self,cache,face,analysis):
        for key,state in [('cache',cache),('face',face),('analysis',analysis)]:
            label,bar=self.bars[key]
            if key=='face' and not self.tools.host.faces.enabled:label.setText('人脸裁剪 · 已关闭');bar.setRange(0,1);bar.setValue(0);continue
            done=state.get('visited',state.get('done',0));total=state.get('total') or 0;bar.setRange(0,max(1,total));bar.setValue(min(done,total));label.setText({'cache':'清晰预览','face':'人脸裁剪','analysis':'分组与质量分析'}[key]+f' · {done:,}/{total:,}'+(' · 运行中' if state.get('running') else ' · 本轮结束'))
        self.cache_note.setText('缓存仅超过 10 GB 才按旧到新清理 70%；切换文件夹清理上一文件夹的派生缓存。\n'+f'预览就绪 {cache.get("ready",0):,} · 失败 {cache.get("errors",0):,} · 容量淘汰 {cache.get("capacity_evictions",0):,}；“本轮结束”不代表磁盘中永久保留全部缓存。')
    def set_groups(self,groups):
        self.table.setModel(GroupModel(groups));header=self.table.horizontalHeader();header.setStretchLastSection(False)
        for index,mode in enumerate((QHeaderView.ResizeMode.ResizeToContents,QHeaderView.ResizeMode.Stretch,QHeaderView.ResizeMode.ResizeToContents)):header.setSectionResizeMode(index,mode)
    def selected_groups(self):return [self.tools.groups[i.row()] for i in self.table.selectionModel().selectedRows() if i.row()<len(self.tools.groups)]
    def selected_ids(self):return [pid for g in self.selected_groups() for pid in g['ids']]
    def mutate(self,ids,**changes):
        if not ids:return
        t=self.tools;records=t.records
        def done(_):t.reset_groups();t.refresh_filters();self.sync_status.setText('已保存；重新打开此页可查看更新后的分组。')
        t.host.op(lambda:records.annotate(ids,**changes),done,t.host.error)
    def name_groups(self):self.mutate(self.selected_ids() or ([self.tools.page.pid()] if self.tools.page.pid() else []),segment=self.segment.text().strip())
    def split(self):
        t=self.tools;pid=t.page.pid()
        if pid:
            records=t.records;ids=t.group_map.get(pid,{}).get('ids',[pid]);t.host.op(lambda:records.split(pid,ids),lambda _:t.refresh_filters(),t.host.error)
    def merge(self):self.mutate(self.selected_ids(),merge_key=secrets.token_hex(8))
    def unmerge(self):self.mutate(self.selected_ids(),merge_key='')
    def undo(self):
        t=self.tools;records=t.records
        def done(_):
            t.refresh_filters()
            if t.auto_xmp:
                for pid,label in getattr(records,'last_undo_labels',{}).items():t.export_one(pid,label=label)
        t.host.op(records.undo,done,t.host.error)
    def refresh(self):
        t=self.tools;t.host.mode_settings('rating').setValue('segment_gap',self.gap.value());t.host.analysis.finished=True;t.host.analysis.start(t.host.library);t.reset_groups();t.refresh_filters()
    def auto_changed(self,value):self.tools.auto_xmp=value;self.tools.host.mode_settings('rating').setValue('auto_xmp',value)
    def export_current(self,force=False):
        t=self.tools;p=t.page;pid=p.pid();info=p.info.get(pid,{})
        if not pid:return
        if info.get('pending'):t.host.error('请等当前评级保存后同步');return
        stars=info.get('stars') if info.get('state')=='ready' else None
        t.export_one(pid,stars, t.annotations.get(pid,{}).get('label',''),force,lambda ok,r:self.sync_status.setText('已同步当前照片' if ok else str(r)))
    def force_current(self):
        if QMessageBox.question(self,'覆盖当前 XMP','将应用的星级和颜色写入当前 XMP？其他后期参数仍会保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:self.export_current(True)
    def import_current(self):
        t=self.tools;p=t.page;pid=p.pid();records=t.records
        if not pid or p.writing:return
        def done(result):
            if result['rating']:p.info[pid]=result['rating'];p.update_caption(pid);p.update_controls()
            t.xmp_errors.pop(pid,None)
            p.reload();self.sync_status.setText('已从 XMP 读取；原有筛选标记不变。')
        t.host.op(lambda:sync_in(records,pid,p.records,p.engine),done,t.host.error)
    def export_all(self):
        t=self.tools
        if t.page.writing or getattr(self,'bulk_running',False):return
        ids=list(t.page.ids);records=t.records;rating=t.page.records;self.bulk_running=True;self.bulk_cancelled=False;self.bulk_done=0;self.bulk_errors={}
        self.sync_status.setText('正在同步当前筛选结果…')
        def pump():
            if self.bulk_cancelled or t.host.closing or t.records is not records or t.host.library is not records.library or self.bulk_done>=len(ids):
                self.bulk_running=False;self.sync_status.setText(f'已处理 {self.bulk_done:,}/{len(ids):,} 张；{len(self.bulk_errors):,} 项需处理。');return
            batch=ids[self.bulk_done:self.bulk_done+16]
            def work():
                cache=rating.cached(batch);annotations=records.annotations(set(batch));result=[]
                for pid in batch:
                    try:sync_out(records,pid,cache.get(pid,{}).get('stars'),annotations.get(pid,{}).get('label',''));result.append((pid,''))
                    except Exception as error:result.append((pid,str(error)))
                return result
            def done(rows):
                self.bulk_done+=len(rows)
                for pid,error in rows:
                    if not error:t.xmp_errors.pop(pid,None)
                self.bulk_errors.update({pid:error for pid,error in rows if error});t.xmp_errors.update(self.bulk_errors)
                self.sync_status.setText(f'正在同步 {self.bulk_done:,}/{len(ids):,} 张；{len(self.bulk_errors):,} 项需处理。');QTimer.singleShot(0,pump)
            def failed(error):self.bulk_running=False;self.sync_status.setText(str(error))
            t.host.op(work,done,failed)
        pump()
    def cancel_bulk(self):self.bulk_cancelled=True
