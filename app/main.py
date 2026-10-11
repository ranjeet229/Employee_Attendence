"""Run: uvicorn app.main:app --port 8000"""
import os
from calendar import monthrange
from datetime import date as D, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import json_util

load_dotenv(); IST=timezone(timedelta(hours=5,minutes=30)); PRESENT=["PRESENT","WFH","ON_DUTY"]
client=MongoClient(os.getenv("MONGO_URI","mongodb://localhost:27017"),tz_aware=True); db=client[os.getenv("MONGO_DB","attendance_db")]
app=FastAPI(title="Employee Attendance & Analytics API",version="2.0.0")
Epoch=Annotated[int,Field(ge=100000000000,le=4102444800000)]; Status=Literal["PRESENT","ABSENT","LEAVE","WFH","ON_DUTY"]

@app.on_event("startup")
def startup():
    db.employees.create_index("emp_code",unique=True,name="employee_code_unique")
    db.employees.create_index([("department",1),("joined_on",1)],name="department_joined")
    db.employees.create_index("joined_on",name="joined_on")
    db.attendance_logs.create_index([("emp_code",1),("date",1)],unique=True,name="attendance_natural_key")
    db.attendance_logs.create_index([("date",-1),("emp_code",1)],name="attendance_list")
    db.attendance_logs.create_index([("date",1),("emp_code",1)],name="analytics_by_date")

def u(x): return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
def ts(x): return (datetime.now(timezone.utc) if x is None else datetime.fromtimestamp(x/1000,timezone.utc)).replace(microsecond=0)
def epoch(x): return None if x is None else int(u(x).timestamp()*1000)
def rnd(x,n=2): return float(Decimal(str(x)).quantize(Decimal('1.'+'0'*n),rounding=ROUND_HALF_UP))
def day(x):
    try:return D.fromisoformat(x)
    except ValueError: raise HTTPException(422,"invalid date")
def month(x):
    try: s=datetime.strptime(x,"%Y-%m").date().replace(day=1);return s,s.replace(day=monthrange(s.year,s.month)[1])
    except ValueError: raise HTTPException(422,"month must be YYYY-MM")
def hm(x):
    try:
        h,m=map(int,x.split(':')); assert 0<=h<24 and 0<=m<60; return h,m
    except: raise HTTPException(422,"invalid time")
def shift(d,x):
    h,m=hm(x);return datetime(d.year,d.month,d.day,h,m,tzinfo=IST)
def adate(pin,e):
    local=u(pin).astimezone(IST); d=local.date()
    if e['shift_end']<=e['shift_start'] and local.time()<shift(d,e['shift_end']).time():d-=timedelta(days=1)
    return d.isoformat()
def calc(pin,pout,e):
    if not pin:return dict(work_hours=None,late_minutes=0,overtime_minutes=0,half_day=False)
    pl=u(pin).astimezone(IST); delta=(pl-shift(pl.date(),e['shift_start'])).total_seconds()
    r=dict(work_hours=None,late_minutes=int(delta//60) if delta>600 else 0,overtime_minutes=0,half_day=False)
    if not pout:return r
    w=rnd((u(pout)-u(pin)).total_seconds()/3600); end=shift(day(adate(pin,e)),e['shift_end'])
    if e['shift_end']<=e['shift_start']:end+=timedelta(days=1)
    ot=int((u(pout).astimezone(IST)-end).total_seconds()//60)
    return dict(work_hours=w,late_minutes=r['late_minutes'],overtime_minutes=ot if ot>=30 else 0,half_day=w<4.5)
def check(pin,pout,e,record_day):
    if pin and adate(pin,e)!=record_day:raise HTTPException(422,"punch_in must be on attendance date")
    if pout and not pin:raise HTTPException(422,"punch_out requires punch_in")
    if pout and (pout<=pin or pout-pin>timedelta(hours=24)):raise HTTPException(422,"invalid punch_out")
def out(d):
    d={k:v for k,v in d.items() if k!='_id'}
    for k in ('created_at','punch_in','punch_out'):
        if k in d:d[k]=epoch(d[k])
    d['half_day']=d.get('half_day',False);d['history']=d.get('history',[])
    for h in d['history']:
        h['at']=epoch(h['at'])
        for c in h['changes'].values():
            for k in ('from','to'):
                if isinstance(c.get(k),datetime):c[k]=epoch(c[k])
    return d

class Employee(BaseModel):
    model_config=ConfigDict(extra='ignore');emp_code:str=Field(pattern=r'^EMP\d{4,6}$');name:str=Field(min_length=1,max_length=100);email:str=Field(max_length=120,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$');department:str=Field(min_length=1,max_length=50);shift_start:str='09:30';shift_end:str='18:30';joined_on:str
    @model_validator(mode='after')
    def v(self):
        hm(self.shift_start);hm(self.shift_end);day(self.joined_on)
        if self.shift_start==self.shift_end:raise ValueError('shift times must differ')
        return self
class In(BaseModel):
    model_config=ConfigDict(extra='ignore');emp_code:str;punched_at:Optional[Epoch]=None;status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
class Out(BaseModel):model_config=ConfigDict(extra='ignore');emp_code:str;punched_at:Optional[Epoch]=None
class Patch(BaseModel):
    model_config=ConfigDict(extra='ignore');status:Optional[Status]=None;punch_in:Optional[Epoch]=None;punch_out:Optional[Epoch]=None;reason:str=Field(min_length=5,max_length=200);regularized_by:str=Field(min_length=1,max_length=50)

@app.get('/health')
def health():
    try:db.command('ping')
    except Exception:raise HTTPException(503,'MongoDB unavailable')
    return {'status':'ok'}
@app.post('/employees',status_code=201)
def create_employee(b:Employee):
    doc=b.model_dump()|{'created_at':datetime.now(timezone.utc)}
    try:db.employees.insert_one(doc)
    except DuplicateKeyError:raise HTTPException(409,'emp_code already exists')
    return out(doc)
@app.get('/employees')
def employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={} if department is None else {'department':department};return {'items':[out(x) for x in db.employees.find(q,{'_id':0}).sort('emp_code',1).skip((page-1)*page_size).limit(page_size)],'total':db.employees.count_documents(q),'page':page,'page_size':page_size}
@app.post('/attendance/punch-in',status_code=201)
def punchin(b:In):
    e=db.employees.find_one({'emp_code':b.emp_code})
    if not e:raise HTTPException(404,'employee not found')
    pin=ts(b.punched_at);doc={'emp_code':b.emp_code,'date':adate(pin,e),'status':b.status,'punch_in':pin,'punch_out':None,'history':[]}|calc(pin,None,e)
    try:db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:raise HTTPException(409,'already punched in for this date')
    return out(doc)
@app.post('/attendance/punch-out')
def punchout(b:Out):
    e=db.employees.find_one({'emp_code':b.emp_code})
    if not e:raise HTTPException(404,'employee not found')
    po=ts(b.punched_at);r=db.attendance_logs.find_one({'emp_code':b.emp_code,'punch_in':{'$lte':po}},sort=[('punch_in',-1)])
    if not r:raise HTTPException(404,'no punch-in found')
    if r.get('punch_out') is not None:raise HTTPException(409,'already punched out')
    check(r['punch_in'],po,e,r['date']); vals={'punch_out':po}|calc(r['punch_in'],po,e)
    r=db.attendance_logs.find_one_and_update({'_id':r['_id'],'punch_out':None},{'$set':vals},return_document=ReturnDocument.AFTER)
    if not r:raise HTTPException(409,'already punched out')
    return out(r)
@app.get('/attendance')
def attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[Status]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if date_from:day(date_from)
    if date_to:day(date_to)
    if date_from and date_to and date_from>date_to:raise HTTPException(422,'date_from after date_to')
    q={};
    if emp_code:q['emp_code']=emp_code
    if status:q['status']=status
    if date_from or date_to:q['date']=({'$gte':date_from} if date_from else {})|({'$lte':date_to} if date_to else {})
    return {'items':[out(x) for x in db.attendance_logs.find(q).sort([('date',-1),('emp_code',1)]).skip((page-1)*page_size).limit(page_size)],'total':db.attendance_logs.count_documents(q),'page':page,'page_size':page_size}
@app.patch('/attendance/{emp_code}/{record_date}')
def regularize(emp_code:str,record_date:str,b:Patch):
    day(record_date);e=db.employees.find_one({'emp_code':emp_code});r=db.attendance_logs.find_one({'emp_code':emp_code,'date':record_date})
    if not e or not r:raise HTTPException(404,'employee or attendance not found')
    status=b.status or r['status'];pin=ts(b.punch_in) if b.punch_in is not None else r.get('punch_in');po=ts(b.punch_out) if b.punch_out is not None else r.get('punch_out')
    if status in ('ABSENT','LEAVE'):
        if b.punch_in is not None or b.punch_out is not None:raise HTTPException(422,'absence cannot have punches')
        pin=po=None
    elif not pin:raise HTTPException(422,'presence needs punch_in')
    check(pin,po,e,record_date);new={'status':status,'punch_in':pin,'punch_out':po}|calc(pin,po,e);fields=['status','punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day']
    changes={f:{'from':r.get(f,False if f=='half_day' else 0),'to':new[f]} for f in fields if r.get(f,False if f=='half_day' else 0)!=new[f]}
    if not changes:raise HTTPException(422,'correction changes nothing')
    h={'at':datetime.now(timezone.utc),'by':b.regularized_by,'reason':b.reason,'changes':changes}
    r=db.attendance_logs.find_one_and_update({'_id':r['_id'],'history':r.get('history',[])},{'$set':new,'$push':{'history':h}},return_document=ReturnDocument.AFTER)
    if not r:raise HTTPException(409,'record changed concurrently')
    return out(r)

def logs_month(code,s,e):return [{'$match':{'emp_code':code,'date':{'$gte':s.isoformat(),'$lte':e.isoformat()}}},{'$addFields':{'wd':{'$dayOfWeek':{'$dateFromString':{'dateString':'$date'}}}}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',PRESENT]},{'$lte':['$wd',6]}]},{'$cond':[{'$ifNull':['$half_day',False]},.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
def workdays(joined,s,e):
    a=max(day(joined),s);return sum((a+timedelta(days=i)).weekday()<5 for i in range((e-a).days+1)) if a<=e else 0
@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str):
    e=db.employees.find_one({'emp_code':emp_code},{'joined_on':1})
    if not e:raise HTTPException(404,'employee not found')
    s,z=globals()['month'](month);r=next(db.attendance_logs.aggregate(logs_month(emp_code,s,z)),{});w=workdays(e['joined_on'],s,z);p=r.get('present_days',0)
    return {'emp_code':emp_code,'month':month,'working_days':w,'present_days':p,'leave_days':r.get('leave_days',0),'late_count':r.get('late_count',0),'total_late_minutes':r.get('total_late_minutes',0),'total_overtime_minutes':r.get('total_overtime_minutes',0),'attendance_pct':rnd(p/w*100) if w else None}

@app.get('/analytics/departments/summary')
def department_summary(month:str,department:Optional[str]=None):
    s,z=globals()['month'](month); match={'joined_on':{'$lte':z.isoformat()}}|({'department':department} if department else {})
    p=[{'$match':match},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',s.isoformat()]},{'$lte':['$date',z.isoformat()]}]}}}],'as':'logs'}},{'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},{'$addFields':{'wd':{'$dayOfWeek':{'$dateFromString':{'dateString':'$logs.date','onNull':datetime(1970,1,1)}}}}},{'$group':{'_id':'$department','people':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',PRESENT]},{'$lte':['$wd',6]}]},{'$cond':[{'$ifNull':['$logs.half_day',False]},.5,1]},0]}},'work':{'$push':{'$cond':[{'$and':[{'$in':['$logs.status',PRESENT]},{'$ne':['$logs.work_hours',None]}]},'$logs.work_hours','$$REMOVE']}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},{'$project':{'_id':0,'department':'$_id','headcount':{'$size':'$people'},'present_days':1,'avg_work_hours':{'$avg':'$work'},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1}},{'$sort':{'department':1}}]
    rows=list(db.employees.aggregate(p))
    for x in rows:
        if x['avg_work_hours'] is not None:x['avg_work_hours']=rnd(x['avg_work_hours'])
    return {'month':month,'items':rows}

@app.get('/analytics/leaderboard/late')
def leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    s,z=globals()['month'](month);p=[{'$match':{'date':{'$gte':s.isoformat(),'$lte':z.isoformat()}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}},{'$match':{'total_late_minutes':{'$gt':0}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'e'}},{'$unwind':'$e'}]
    if department:p.append({'$match':{'e.department':department}})
    p += [{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$project':{'_id':0,'rank':1,'emp_code':'$_id','name':'$e.name','department':'$e.department','total_late_minutes':1,'late_count':1}},{'$sort':{'total_late_minutes':-1,'emp_code':1}}]
    return {'month':month,'items':list(db.attendance_logs.aggregate(p))}

@app.get('/analytics/departments/{department}/trend')
def trend(department:str,from_:str=Query(alias='from'),to:str=Query(...)):
    s,z=day(from_),day(to)
    if z<s or (z-s).days>92:raise HTTPException(422,'invalid range')
    if not db.employees.find_one({'department':department}):raise HTTPException(404,'department not found')
    # Generate the requested calendar in MongoDB; logs are joined by date so empty days remain.
    p=[{'$documents':[{}]},{'$project':{'dates':{'$map':{'input':{'$range':[0,(z-s).days+1]},'as':'i','in':{'$dateAdd':{'startDate':datetime(s.year,s.month,s.day),'unit':'day','amount':'$$i'}}}}}},{'$unwind':'$dates'},{'$project':{'date':{'$dateToString':{'date':'$dates','format':'%Y-%m-%d'}},'wd':{'$dayOfWeek':'$dates'}}},{'$lookup':{'from':'employees','let':{'d':'$date'},'pipeline':[{'$match':{'department':department,'$expr':{'$lte':['$joined_on','$$d']}}},{'$count':'n'}],'as':'hc'}},{'$lookup':{'from':'attendance_logs','let':{'d':'$date'},'pipeline':[{'$match':{'$expr':{'$eq':['$date','$$d']}}},{'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'e'}},{'$unwind':'$e'},{'$match':{'e.department':department}},{'$group':{'_id':None,'present_count':{'$sum':{'$cond':[{'$in':['$status',PRESENT]},{'$cond':[{'$ifNull':['$half_day',False]},.5,1]},0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}],'as':'a'}},{'$project':{'_id':0,'date':1,'is_working_day':{'$lte':['$wd',6]},'headcount':{'$ifNull':[{'$arrayElemAt':['$hc.n',0]},0]},'present_count':{'$ifNull':[{'$arrayElemAt':['$a.present_count',0]},0]},'late_count':{'$ifNull':[{'$arrayElemAt':['$a.late_count',0]},0]}}},{'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$divide':['$present_count','$headcount']},None]}}},{'$setWindowFields':{'sortBy':{'date':1},'output':{'ma':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},{'$project':{'date':1,'is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':1,'moving_avg_7d':'$ma'}}]
    rows=list(db.aggregate(p))
    for r in rows:
        for k in ('attendance_rate','moving_avg_7d'):
            if r[k] is not None:r[k]=rnd(r[k],4)
    return {'department':department,'items':rows}

@app.get('/admin/explain/{endpoint}')
def explain(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=10,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[Status]=None,from_:Optional[str]=Query(None,alias='from'),to:Optional[str]=None,page:int=1,page_size:int=20):
    if endpoint=='attendance_list':
        q={};
        if emp_code:q['emp_code']=emp_code
        if status:q['status']=status
        if date_from or date_to:q['date']=({'$gte':date_from} if date_from else {})|({'$lte':date_to} if date_to else {})
        command={'explain':{'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size},'verbosity':'executionStats'}; coll='attendance_logs'
    elif endpoint=='employee_monthly':
        if not emp_code or not month:raise HTTPException(422,'emp_code and month required')
        s,z=globals()['month'](month);command={'explain':{'aggregate':'attendance_logs','pipeline':logs_month(emp_code,s,z),'cursor':{}},'verbosity':'executionStats'};coll='attendance_logs'
    elif endpoint=='department_summary':
        if not month:raise HTTPException(422,'month required')
        s,z=globals()['month'](month);match={'joined_on':{'$lte':z.isoformat()}}|({'department':department} if department else {})
        pipeline=[{'$match':match},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',s.isoformat()]},{'$lte':['$date',z.isoformat()]}]}}}],'as':'logs'}}]
        command={'explain':{'aggregate':'employees','pipeline':pipeline,'cursor':{}},'verbosity':'executionStats'};coll='employees'
    elif endpoint=='late_leaderboard':
        if not month:raise HTTPException(422,'month required')
        s,z=globals()['month'](month);pipeline=[{'$match':{'date':{'$gte':s.isoformat(),'$lte':z.isoformat()}}},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}}}},{'$match':{'total_late_minutes':{'$gt':0}}},{'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'employee'}},{'$unwind':'$employee'}]
        if department:pipeline.append({'$match':{'employee.department':department}})
        pipeline += [{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}}]
        command={'explain':{'aggregate':'attendance_logs','pipeline':pipeline,'cursor':{}},'verbosity':'executionStats'};coll='attendance_logs'
    elif endpoint=='department_trend':
        if not department or not from_ or not to:raise HTTPException(422,'department, from and to required')
        s,z=day(from_),day(to)
        if z<s or (z-s).days>92:raise HTTPException(422,'invalid range')
        # The trend's indexed attendance scan; employee lookups and calendar expansion follow it.
        pipeline=[{'$match':{'date':{'$gte':s.isoformat(),'$lte':z.isoformat()}}},{'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'employee'}},{'$unwind':'$employee'},{'$match':{'employee.department':department}}]
        command={'explain':{'aggregate':'attendance_logs','pipeline':pipeline,'cursor':{}},'verbosity':'executionStats'};coll='attendance_logs'
    else: raise HTTPException(422,'unknown endpoint')
    # Explain output can contain BSON-specific numeric and identifier values which
    # FastAPI's default encoder cannot always serialize.
    payload={'endpoint':endpoint,'collection':coll,'explain':db.command(command)}
    return Response(content=json_util.dumps(payload),media_type='application/json')
