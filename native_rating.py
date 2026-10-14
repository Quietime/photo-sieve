"""Windows System.Rating metadata. No Qt imports; intended for an isolated helper process."""
import os
from pathlib import Path

VALUES=(0,1,25,50,75,99)
class RatingError(Exception):pass

def stars_from_value(value):
    value=int(value or 0)
    if value<0 or value>99:raise RatingError('Windows 评级数值不在 0～99 范围内')
    return 0 if value==0 else min(5,(value+12)//25+1)

def value_from_stars(stars):
    if isinstance(stars,bool) or not isinstance(stars,int) or not 0<=stars<=5:raise RatingError('星级必须为 0～5')
    return VALUES[stars]

def _operate_windows(path,stars=None,expected=None):
    if os.name!='nt':raise RatingError('Windows 系统评级只能在 Windows 上读写')
    import pythoncom
    from win32com.propsys import propsys,pscon
    path=str(Path(path).resolve())
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    store=writer=capabilities=None
    try:
        store=propsys.SHGetPropertyStoreFromParsingName(path,None,0)
        raw=store.GetValue(pscon.PKEY_Rating).GetValue()
        before=stars_from_value(raw)
        if expected is not None and before!=expected:raise RatingError('Windows 星级已被改变，不能覆盖外部修改来撤销评级')
        store=None
        writable=False;reason=''
        try:
            writer=propsys.SHGetPropertyStoreFromParsingName(path,None,2)
            capabilities=writer.QueryInterface(propsys.IID_IPropertyStoreCapabilities)
            writable=bool(capabilities.IsPropertyWritable(pscon.PKEY_Rating));capabilities=None
            if not writable:reason='Windows 当前的文件属性处理器不支持为此格式写入星级'
        except Exception as exc:
            reason='Windows 无法以可写方式打开此文件的评级属性：'+str(exc)
        if stars is not None:
            value=value_from_stars(stars)
            if not writable:raise RatingError(reason)
            writer.SetValue(pscon.PKEY_Rating,propsys.PROPVARIANTType(value,pythoncom.VT_UI4))
            writer.Commit();writer=None
            store=propsys.SHGetPropertyStoreFromParsingName(path,None,0)
            raw=store.GetValue(pscon.PKEY_Rating).GetValue()
            if stars_from_value(raw)!=stars:raise RatingError('Windows 写入后的星级验证未通过')
        return {'stars':stars_from_value(raw),'value':int(raw or 0),'before':before,'writable':writable,'reason':reason,'backend':'windows','windows_visible':True}
    except pythoncom.com_error as exc:
        raise RatingError('Windows 无法读取此格式的系统星级；请检查对应的文件属性处理器（错误码 '+hex(exc.hresult & 0xffffffff)+'）') from exc
    finally:
        store=writer=capabilities=None;pythoncom.CoUninitialize()


def operate(path,stars=None,expected=None):
    if Path(path).suffix.lower()!='.arw':return _operate_windows(path,stars,expected)
    if os.name!='nt':raise RatingError('Windows 系统评级只能在 Windows 上读写')
    from embedded_rating import executable,read,write,EmbeddedRatingError
    try:executable()
    except EmbeddedRatingError:
        # Older/source-only installs can still use a functioning Shell handler.
        return _operate_windows(path,stars,expected)
    try:
        embedded=read(path) if stars is None else write(path,stars,expected)
    except EmbeddedRatingError as exc:raise RatingError(str(exc)) from exc
    actual=embedded['stars']
    try:
        shell=_operate_windows(path)
        visible=(shell['stars']==actual) if actual else None
    except RatingError:visible=False
    reason=('星级已写入 ARW 原图；Windows 资源管理器可读取' if visible is True else
            '星级保存在 ARW 原图；Windows 当前的 RAW 属性处理器未读取此星级' if visible is False else
            '星级保存在 ARW 原图；无星级时无法确认 Windows 是否支持显示')
    return {'stars':actual,'value':value_from_stars(actual),'before':embedded.get('before',actual),
            'writable':os.access(path,os.W_OK),'reason':reason,'backend':'embedded','windows_visible':visible}
