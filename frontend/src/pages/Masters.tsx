/** Customers, suppliers, items and the GST rate table. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fmtDate, inr, pct } from '../lib/api'
import type { Customer, Supplier } from '../lib/types'
import { ErrorBox, Field, Loading, Page } from '../components/ui'

interface Item {
  id: string
  sku: string
  description: string
  hsn_code: string
  uom: string
  default_unit_price: string | null
  is_service: boolean
}

interface TaxRate {
  id: string
  hsn_code: string
  effective_from: string
  effective_to: string | null
  rate_percent: string
  cess_percent: string
  description: string | null
}

type Tab = 'customers' | 'suppliers' | 'items' | 'tax-rates'

export default function Masters() {
  const [tab, setTab] = useState<Tab>('customers')

  return (
    <Page title="Masters" subtitle="Customers, suppliers, items and GST rates">
      <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
        {(['customers', 'suppliers', 'items', 'tax-rates'] as Tab[]).map((key) => (
          <button
            key={key}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === key
                ? 'border-brand-600 text-brand-700'
                : 'border-transparent text-ink-mute hover:text-ink-soft'
            }`}
            onClick={() => setTab(key)}
          >
            {key.replace('-', ' ')}
          </button>
        ))}
      </div>

      {tab === 'customers' && <Customers />}
      {tab === 'suppliers' && <Suppliers />}
      {tab === 'items' && <Items />}
      {tab === 'tax-rates' && <TaxRates />}
    </Page>
  )
}

function GstinField({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const check = useQuery({
    queryKey: ['gstin', value],
    queryFn: () => api.get<{ valid: boolean; reason?: string; state_name?: string }>(
      `/gstin/validate?value=${encodeURIComponent(value)}`,
    ),
    enabled: value.length === 15,
  })

  return (
    <Field
      label="GSTIN"
      hint={
        value.length === 15 && check.data ? (
          check.data.valid ? (
            <span className="text-emerald-700">✓ valid — {check.data.state_name}</span>
          ) : (
            <span className="text-red-700">✗ {check.data.reason}</span>
          )
        ) : (
          'The state code drives the CGST/SGST vs IGST split, so the checksum is verified.'
        )
      }
    >
      <input
        className="input tnum uppercase"
        maxLength={15}
        value={value}
        placeholder="24AAACC1206D1ZM"
        onChange={(e) => onChange(e.target.value.toUpperCase())}
      />
    </Field>
  )
}

function Customers() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', gstin: '', billing_address: '' })
  const list = useQuery({ queryKey: ['customers'], queryFn: () => api.get<Customer[]>('/customers') })

  const create = useMutation({
    mutationFn: () =>
      api.post<Customer>('/customers', {
        name: form.name,
        gstin: form.gstin || null,
        billing_address: form.billing_address || null,
      }),
    onSuccess: () => {
      setForm({ name: '', gstin: '', billing_address: '' })
      queryClient.invalidateQueries({ queryKey: ['customers'] })
    },
  })

  if (list.isLoading) return <Loading what="customers" />

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card card-pad">
        <h2 className="mb-3 text-sm font-semibold">Add a customer</h2>
        <div className="space-y-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <GstinField value={form.gstin} onChange={(gstin) => setForm({ ...form, gstin })} />
          <Field label="Billing address">
            <textarea
              className="input"
              rows={3}
              value={form.billing_address}
              onChange={(e) => setForm({ ...form, billing_address: e.target.value })}
            />
          </Field>
          {create.error && <ErrorBox error={create.error} />}
          <button className="btn-primary w-full" disabled={!form.name || create.isPending} onClick={() => create.mutate()}>
            Add customer
          </button>
        </div>
      </div>

      <div className="card overflow-hidden lg:col-span-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead>
              <tr>
                <th className="th">Name</th>
                <th className="th">GSTIN</th>
                <th className="th">State</th>
                <th className="th">Place of supply</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((customer) => (
                <tr key={customer.id}>
                  <td className="td font-medium">{customer.name}</td>
                  <td className="td tnum">{customer.gstin ?? <span className="text-ink-mute">unregistered</span>}</td>
                  <td className="td">{customer.state_name ?? '—'}</td>
                  <td className="td tnum">{customer.place_of_supply_state_code ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Suppliers() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', gstin: '', billing_address: '' })
  const list = useQuery({ queryKey: ['suppliers'], queryFn: () => api.get<Supplier[]>('/suppliers') })

  const create = useMutation({
    mutationFn: () =>
      api.post<Supplier>('/suppliers', {
        name: form.name,
        gstin: form.gstin || null,
        billing_address: form.billing_address || null,
      }),
    onSuccess: () => {
      setForm({ name: '', gstin: '', billing_address: '' })
      queryClient.invalidateQueries({ queryKey: ['suppliers'] })
    },
  })

  if (list.isLoading) return <Loading what="suppliers" />

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card card-pad">
        <h2 className="mb-3 text-sm font-semibold">Add a supplier</h2>
        <div className="space-y-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <GstinField value={form.gstin} onChange={(gstin) => setForm({ ...form, gstin })} />
          <Field label="Address">
            <textarea
              className="input"
              rows={3}
              value={form.billing_address}
              onChange={(e) => setForm({ ...form, billing_address: e.target.value })}
            />
          </Field>
          {create.error && <ErrorBox error={create.error} />}
          <button className="btn-primary w-full" disabled={!form.name || create.isPending} onClick={() => create.mutate()}>
            Add supplier
          </button>
        </div>
      </div>

      <div className="card overflow-hidden lg:col-span-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px]">
            <thead>
              <tr>
                <th className="th">Name</th>
                <th className="th">GSTIN</th>
                <th className="th">State</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((supplier) => (
                <tr key={supplier.id}>
                  <td className="td font-medium">{supplier.name}</td>
                  <td className="td tnum">{supplier.gstin ?? '—'}</td>
                  <td className="td">{supplier.state_name ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Items() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ sku: '', description: '', hsn_code: '', uom: 'NOS', default_unit_price: '' })
  const list = useQuery({ queryKey: ['items'], queryFn: () => api.get<Item[]>('/items') })

  const create = useMutation({
    mutationFn: () =>
      api.post<Item>('/items', {
        ...form,
        default_unit_price: form.default_unit_price || null,
      }),
    onSuccess: () => {
      setForm({ sku: '', description: '', hsn_code: '', uom: 'NOS', default_unit_price: '' })
      queryClient.invalidateQueries({ queryKey: ['items'] })
    },
  })

  if (list.isLoading) return <Loading what="items" />

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card card-pad">
        <h2 className="mb-3 text-sm font-semibold">Add an item</h2>
        <div className="space-y-3">
          <Field label="SKU">
            <input className="input" value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} />
          </Field>
          <Field label="Description">
            <textarea className="input" rows={2} value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <Field label="HSN / SAC" hint="Mandatory. Lithium-ion cells and packs are 8507.">
            <input className="input tnum" value={form.hsn_code}
              onChange={(e) => setForm({ ...form, hsn_code: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="UOM">
              <input className="input" value={form.uom} onChange={(e) => setForm({ ...form, uom: e.target.value })} />
            </Field>
            <Field label="Default rate">
              <input className="input tnum" value={form.default_unit_price}
                onChange={(e) => setForm({ ...form, default_unit_price: e.target.value })} />
            </Field>
          </div>
          {create.error && <ErrorBox error={create.error} />}
          <button
            className="btn-primary w-full"
            disabled={!form.sku || !form.description || !form.hsn_code || create.isPending}
            onClick={() => create.mutate()}
          >
            Add item
          </button>
        </div>
      </div>

      <div className="card overflow-hidden lg:col-span-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead>
              <tr>
                <th className="th">SKU</th>
                <th className="th">Description</th>
                <th className="th">HSN</th>
                <th className="th">UOM</th>
                <th className="th text-right">Default rate</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td className="td font-medium">{item.sku}</td>
                  <td className="td">{item.description}</td>
                  <td className="td tnum">{item.hsn_code}</td>
                  <td className="td">{item.uom}</td>
                  <td className="td tnum text-right">{inr(item.default_unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function TaxRates() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    hsn_code: '',
    effective_from: new Date().toISOString().slice(0, 10),
    rate_percent: '18',
    cess_percent: '0',
    description: '',
  })
  const list = useQuery({ queryKey: ['tax-rates'], queryFn: () => api.get<TaxRate[]>('/tax-rates') })

  const create = useMutation({
    mutationFn: () => api.post<TaxRate>('/tax-rates', { ...form, description: form.description || null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tax-rates'] }),
  })

  if (list.isLoading) return <Loading what="tax rates" />

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card card-pad">
        <h2 className="mb-3 text-sm font-semibold">Add a rate</h2>
        <p className="mb-3 text-xs text-ink-mute">
          Rates are keyed by HSN and date. Adding a new one closes the previous open-ended row, so an
          old invoice still reprints at the rate that applied on its own date.
        </p>
        <div className="space-y-3">
          <Field label="HSN / SAC">
            <input className="input tnum" value={form.hsn_code}
              onChange={(e) => setForm({ ...form, hsn_code: e.target.value })} />
          </Field>
          <Field label="Effective from">
            <input className="input" type="date" value={form.effective_from}
              onChange={(e) => setForm({ ...form, effective_from: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="GST %">
              <input className="input tnum" value={form.rate_percent}
                onChange={(e) => setForm({ ...form, rate_percent: e.target.value })} />
            </Field>
            <Field label="Cess %">
              <input className="input tnum" value={form.cess_percent}
                onChange={(e) => setForm({ ...form, cess_percent: e.target.value })} />
            </Field>
          </div>
          {create.error && <ErrorBox error={create.error} />}
          <button className="btn-primary w-full" disabled={!form.hsn_code || create.isPending}
            onClick={() => create.mutate()}>
            Add rate
          </button>
        </div>
      </div>

      <div className="card overflow-hidden lg:col-span-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead>
              <tr>
                <th className="th">HSN</th>
                <th className="th">From</th>
                <th className="th">To</th>
                <th className="th text-right">GST</th>
                <th className="th text-right">Cess</th>
                <th className="th">Description</th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((rate) => (
                <tr key={rate.id}>
                  <td className="td tnum font-medium">{rate.hsn_code}</td>
                  <td className="td tnum">{fmtDate(rate.effective_from)}</td>
                  <td className="td tnum">
                    {rate.effective_to ? fmtDate(rate.effective_to) : <span className="chip bg-emerald-50 text-emerald-700">current</span>}
                  </td>
                  <td className="td tnum text-right">{pct(rate.rate_percent)}</td>
                  <td className="td tnum text-right">{pct(rate.cess_percent)}</td>
                  <td className="td text-ink-mute">{rate.description ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
