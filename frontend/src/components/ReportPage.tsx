import React, { useState } from 'react';

// Токенов во фронтенде нет: сессию держит bionicpro-auth, браузер носит
// только HttpOnly cookie, поэтому запрос идёт с credentials: 'include'.
const API = process.env.REACT_APP_API_URL || '';

type Day = {
  day: string; movements: number; avg_battery: number;
  min_battery: number; errors: number; active_hours: number;
};
type Report = {
  user: string; full_name: string; prosthesis_model: string;
  period: { from: string; to: string; processed_to: string };
  days: Day[];
};

const ReportPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);
      setReport(null);

      const response = await fetch(`${API}/reports`, { credentials: 'include' });
      if (response.status === 401) {
        setError('Not authenticated');
        return;
      }
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `Request failed: ${response.status}`);
      }
      const { url } = await response.json();
      setReport(await (await fetch(url)).json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md">
        <h1 className="text-2xl font-bold mb-6">Usage Reports</h1>

        <a
          href={`${API}/auth/login`}
          className="inline-block mr-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Login
        </a>

        <button
          onClick={downloadReport}
          disabled={loading}
          className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
            loading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          {loading ? 'Generating Report...' : 'Download Report'}
        </button>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}

        {report && (
          <div className="mt-4">
            <p>{report.full_name} ({report.user}), {report.prosthesis_model}</p>
            <p>Period: {report.period.from} — {report.period.to} (processed up to {report.period.processed_to})</p>
            <table className="mt-2 text-sm">
              <thead>
                <tr>
                  <th className="px-2">Day</th><th className="px-2">Movements</th><th className="px-2">Avg battery</th>
                  <th className="px-2">Min battery</th><th className="px-2">Errors</th><th className="px-2">Active hours</th>
                </tr>
              </thead>
              <tbody>
                {report.days.map((d) => (
                  <tr key={d.day}>
                    <td className="px-2">{d.day}</td><td className="px-2">{d.movements}</td><td className="px-2">{d.avg_battery}</td>
                    <td className="px-2">{d.min_battery}</td><td className="px-2">{d.errors}</td><td className="px-2">{d.active_hours}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;
