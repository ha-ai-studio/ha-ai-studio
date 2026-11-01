import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { readSettings, SchemaEditor } from './SchemaEditor';
afterEach(() => vi.unstubAllGlobals());
it('selects a sensor using the shared picker without submitting or changing other draft settings', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ok:true,json:async()=>({total:1,entities:[{
    entity_id:'sensor.water',name:'Water meter',state:'123.4',available:true,
  }]})});
  vi.stubGlobal('fetch',fetchMock);
  const submit=vi.fn((e)=>e.preventDefault());
  function Form() {
    const [values,setValues]=useState<Record<string,string|boolean>>({period:'24'});
    return <form onSubmit={submit}><SchemaEditor schema={{type:'object',additionalProperties:false,
      properties:{entity:{type:'string',title:'Water meter entity','x-ha-entity-domains':['sensor']},
                  period:{type:'integer',title:'Period'}}}}
      initial={{entity:'sensor.previous'}} values={values} onValues={setValues} raw="" onRaw={()=>{}} /></form>;
  }
  render(<Form/>);
  const user=userEvent.setup();
  await user.click(screen.getByRole('button',{name:'Choose Water meter entity'}));
  await user.type(screen.getByLabelText('Search entities'),'water{Enter}');
  await user.click(await screen.findByRole('button',{name:/Select Water meter \(sensor.water\)/}));
  expect(screen.getByLabelText('Water meter entity')).toHaveValue('sensor.water');
  expect(screen.getByLabelText('Period')).toHaveValue(24);
  expect(submit).not.toHaveBeenCalled();
  await waitFor(()=>expect(String(fetchMock.mock.lastCall?.[0])).toContain('domains=sensor'));
  await user.click(screen.getByRole('button',{name:'Choose Water meter entity'}));
  await user.click(screen.getByRole('button',{name:'Cancel'}));
  expect(screen.getByLabelText('Water meter entity')).toHaveValue('sensor.water');
  await user.click(screen.getByRole('button',{name:'Clear Water meter entity'}));
  expect(screen.getByLabelText('Water meter entity')).toHaveValue('');
  await user.click(screen.getByLabelText('Water meter entity'));
  expect(screen.getByLabelText('Search entities')).toBeVisible();
  expect(submit).not.toHaveBeenCalled();
});

it('displays numeric defaults and saves them only when missing without overriding edits', () => {
  const schema = {type:'object',additionalProperties:false,required:['hours'],properties:{
    hours:{type:'integer',title:'History hours',default:24},
    rate:{type:'number',title:'Rate',default:1},
  }};
  const initial={rate:0};
  const onValues=vi.fn();
  render(<SchemaEditor schema={schema} initial={initial} values={{}} raw="" onValues={onValues} onRaw={()=>{}}/>);
  expect(screen.getByLabelText('History hours')).toHaveValue(24);
  expect(screen.getByLabelText('Rate')).toHaveValue(0);
  expect(onValues).not.toHaveBeenCalled();
  expect(initial).toEqual({rate:0});
  expect(readSettings(schema,initial,{},'')).toEqual({hours:24,rate:0});
  expect(readSettings(schema,{hours:12},{rate:'2'},'')).toEqual({hours:12,rate:2});
  expect(()=>readSettings(schema,{}, {hours:''},'')).toThrow('History hours is required.');
  expect(readSettings(schema,{}, {rate:''},'')).toEqual({hours:24});
});
