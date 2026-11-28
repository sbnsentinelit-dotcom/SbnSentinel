'use client';
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AuthScreen } from '@/components/Auth/AuthScreen';
import { BootScreen } from '@/components/CommandCenter/BootScreen';
import { signOut } from '@/utils/signOut';

export default function RootPage() {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    const role = localStorage.getItem('userRole');
    const params = new URLSearchParams(window.location.search);

    // An invitation or password-reset link is for the person it was sent to: whoever is signed in
    // in this browser is signed out (on the server too), and the activation / reset screen opens.
    if (params.get('invite_token') || params.get('reset_token')) {
      if (token) {
        signOut().finally(() => setIsLoading(false));
      } else {
        setIsLoading(false);
      }
      return;
    }

    if (token) {
      if (role === 'super_admin' || role === 'System Administrator') {
        router.push('/super-admin');
      } else {
        router.push('/dashboard');
      }
    } else {
      setIsLoading(false);
    }
  }, [router]);

  if (isLoading) {
    return <BootScreen onComplete={() => setIsLoading(false)} />;
  }

  return (
    <AuthScreen 
      onLogin={(role: string, user: any) => { 
        if (role === 'super_admin' || role === 'System Administrator') {
          sessionStorage.setItem('just_logged_in', 'true');
          router.push('/super-admin');
        } else {
          sessionStorage.setItem('just_logged_in', 'true');
          router.push('/dashboard');
        }
      }} 
    />
  );
}
