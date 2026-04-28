import { Route, Routes } from 'react-router-dom'
import MemvaultLayout from '../components/MemvaultLayout'
import MemoryBrowser from './browser'
import GalaxyPage from './galaxy'

export default function MemvaultPages() {
  return (
    <Routes>
      <Route element={<MemvaultLayout />}>
        <Route index element={<MemoryBrowser />} />
        <Route path="galaxy" element={<GalaxyPage />} />
      </Route>
    </Routes>
  )
}
