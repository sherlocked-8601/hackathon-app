import React from 'react'
import { featureCheckNote, safeWebsite, taskChecked } from '../toolEvidence'
import { FavouriteButton } from '../favourites/Favourites'
import './businessToolkit.css'

const featureOnly = (tool, capability) => Array.isArray(tool.feature_checked?.[capability]) ? tool.feature_checked[capability] : null

export function businessToolkitGroups(tools, categories, tradeId) {
  const eligible = tools.filter(tool => tool.applicability === 'cross_industry' || (tradeId && tool.applicability === 'restricted' && tool.trades.includes(tradeId)))
  return categories.map(category => ({
    ...category,
    tools: eligible.map(tool => {
      const inCategory = tool.capabilities.filter(capability => category.capabilities.includes(capability.capability) && category.labels?.[capability.capability])
      const purposes = tool.verification_state === 'refresh_due' ? [] : inCategory.filter(capability => taskChecked(tool, capability.capability)
        && capability.access?.status === 'present'
        && ['included', 'add_on'].includes(capability.access.value)
        && /^https?:\/\//i.test(capability.access.source_url || ''))
      const qualified = inCategory.filter(capability => !purposes.includes(capability) && featureOnly(tool, capability.capability))
      return {
        tool,
        purposes: purposes.map(capability => category.labels[capability.capability]),
        qualified: qualified.map(capability => ({ label: category.labels[capability.capability], note: featureCheckNote({ feature_verified: true, unconfirmed: featureOnly(tool, capability.capability) }) })),
      }
    }).filter(entry => entry.purposes.length || entry.qualified.length),
  })).filter(category => category.tools.length)
}

export function tradeSpecificCount(groups) {
  return new Set(groups.flatMap(group => group.tools.filter(({ tool }) => tool.applicability === 'restricted').map(({ tool }) => tool.id))).size
}

export default function BusinessToolkit({ business, tradeId, tools, categories, onCategory, onBrowse }) {
  if (!business) return null
  const groups = businessToolkitGroups(tools, categories, tradeId)
  const generalOnly = Boolean(tradeId) && groups.length > 0 && tradeSpecificCount(groups) === 0
  const label = business === business.toUpperCase() ? business : business.toLowerCase()
  return (
    <section className="business-toolkit" aria-labelledby="business-toolkit-title">
      <div className="business-toolkit-heading">
        <div><p className="section-kicker">GET TO KNOW YOUR OPTIONS</p><h3 id="business-toolkit-title">Your {tradeId ? label : 'business'} toolkit</h3></div>
        <span className="business-toolkit-scope">{groups.length} {groups.length === 1 ? 'area' : 'areas'} to explore</span>
      </div>
      <p className="business-toolkit-note">See what each type of tool helps with and examples from our reviewed catalog. These are options to explore, not subscriptions every business needs.</p>
      {!tradeId && <p className="business-toolkit-note">We haven't mapped your business type yet. These examples are general-purpose tools.</p>}
      {generalOnly && <p className="business-toolkit-note" role="status">Limited coverage right now: none of the {label} tools in our catalog have a vendor check from the last 7 days, so only general-purpose tools are shown. Other businesses see the same list.</p>}
      <div className="business-toolkit-grid">
        {groups.map((group, index) => <article className="business-toolkit-card" key={group.id}>
          <div className="business-toolkit-category"><span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><h4>{group.name}</h4></div>
          <ul>{group.tools.map(({ tool, purposes, qualified }) => <li key={tool.id}>
            <div>{safeWebsite(tool.url) ? <a href={safeWebsite(tool.url)} target="_blank" rel="noopener noreferrer">{tool.name} <span aria-hidden="true">&#8599;</span></a> : <strong>{tool.name}</strong>}
              {purposes.length > 0 && <><p>{purposes.join(', ')}</p><small>{tool.applicability === 'cross_industry' ? 'General-purpose' : `Lists ${label}`}. Checked for these tasks</small></>}
              {qualified.map(item => <React.Fragment key={item.label}><p>{item.label}</p><small className="business-toolkit-feature-check">{tool.applicability === 'cross_industry' ? 'General-purpose' : `Lists ${label}`}. {item.note}</small></React.Fragment>)}
            </div>
            <FavouriteButton toolId={tool.id} name={tool.name} />
          </li>)}</ul>
          <button type="button" onClick={() => onCategory(group.id)}>Find tools for this work <span aria-hidden="true">&#8594;</span></button>
        </article>)}
      </div>
      {!groups.length && <p className="business-toolkit-note">We haven't checked examples for this business yet. Browse the catalog or choose a task to see what we can confirm.</p>}
      <div className="business-toolkit-footer"><p>Fit, features and pricing can depend on your plan. Choose a task to check the details. This overview covers our catalog, not every tool on the market.</p><button type="button" onClick={onBrowse}>Browse the full catalog <span aria-hidden="true">&#8594;</span></button></div>
    </section>
  )
}
