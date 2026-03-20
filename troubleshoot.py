#!/usr/bin/env python3
"""
Email Connection Troubleshooting Script
Tests IMAP and SMTP connections separately
"""

import imaplib
import smtplib
import sys
from getpass import getpass

import config


def test_exchange():
    """Test Exchange connection"""
    print("\n" + "=" * 60)
    print("TESTING EXCHANGE WEB SERVICES (EWS)")
    print("=" * 60)
    
    print(f"Email: {config.EMAIL_ADDRESS}")
    print(f"Password: {'*' * len(config.EMAIL_PASSWORD) if config.EMAIL_PASSWORD else 'NOT SET'}")
    print(f"Autodiscover: {config.EXCHANGE_AUTODISCOVER}")
    if not config.EXCHANGE_AUTODISCOVER:
        print(f"Server: {config.EXCHANGE_SERVER}")
        print(f"Version: {config.EXCHANGE_VERSION}")
    
    if 'your-' in config.EMAIL_ADDRESS or 'your-' in config.EMAIL_PASSWORD:
        print("\n❌ ERROR: Email credentials not configured!")
        print("   Edit your .env file with real credentials")
        return False
    
    try:
        from exchangelib import Credentials, Account, DELEGATE, Configuration
        
        print("\nConnecting to Exchange...")
        
        # Use EXCHANGE_USERNAME if set, otherwise use EMAIL_ADDRESS
        username = config.EXCHANGE_USERNAME if hasattr(config, 'EXCHANGE_USERNAME') and config.EXCHANGE_USERNAME else config.EMAIL_ADDRESS
        
        print(f"Authentication username: {username}")
        print(f"Primary email (mailbox): {config.EMAIL_ADDRESS}")
        
        credentials = Credentials(username=username, password=config.EMAIL_PASSWORD)
        
        def _resolve_version(name: str):
            from exchangelib import Version, Build

            version_map = {
                'Exchange2013': Version(build=Build(15, 0, 847, 32)),
                'Exchange2016': Version(build=Build(15, 1, 845, 34)),
                'Exchange2019': Version(build=Build(15, 2, 858, 5)),
                # Office365 -> None so exchangelib autodetects
            }
            return version_map.get(name)

        if config.EXCHANGE_AUTODISCOVER:
            print("Using autodiscover...")
            account = Account(
                primary_smtp_address=config.EMAIL_ADDRESS,
                credentials=credentials,
                autodiscover=True,
                access_type=DELEGATE
            )
        else:
            print(f"Connecting to {config.EXCHANGE_SERVER}...")
            version = _resolve_version(config.EXCHANGE_VERSION)
            config_kwargs = {
                'server': config.EXCHANGE_SERVER,
                'credentials': credentials,
            }
            if version:
                config_kwargs['version'] = version
            
            server_config = Configuration(**config_kwargs)
            
            account = Account(
                primary_smtp_address=config.EMAIL_ADDRESS,
                config=server_config,
                autodiscover=False,
                access_type=DELEGATE
            )
        
        print("✓ Connected to Exchange")
        
        # Test inbox access
        print("Testing inbox access...")
        total = account.inbox.total_count
        unread = account.inbox.unread_count
        print(f"✓ Inbox accessible: {total} total emails, {unread} unread")
        
        # Get a sample email
        print("Fetching sample email...")
        items = list(account.inbox.all().order_by('-datetime_received')[:1])
        if items:
            item = items[0]
            print(f"✓ Sample email: '{item.subject}' from {item.sender.email_address if item.sender else 'Unknown'}")
        
        print("\n✅ Exchange connection is working!")
        return True
        
    except ImportError:
        print("\n❌ ERROR: exchangelib not installed")
        print("   Install it with: uv pip install -r pyproject.toml")
        return False
    except Exception as e:
        print(f"\n❌ Exchange Error: {e}")
        print("\nCommon fixes:")
        print("  1. Verify email and password are correct")
        print("  2. Try autodiscover (EXCHANGE_AUTODISCOVER=true)")
        print("  3. Check if your university uses Office365:")
        print("     EXCHANGE_SERVER=outlook.office365.com")
        print("     EXCHANGE_VERSION=Office365")
        print("  4. May need VPN if accessing from off-campus")
        print("  5. Check if account requires app password or OAuth2")
        return False


def test_imap():
    """Test IMAP connection"""
    print("\n" + "=" * 60)
    print("TESTING IMAP CONNECTION")
    print("=" * 60)
    
    print(f"Server: {config.IMAP_SERVER}")
    print(f"Port: {config.IMAP_PORT}")
    print(f"Email: {config.EMAIL_ADDRESS}")
    print(f"Password: {'*' * len(config.EMAIL_PASSWORD) if config.EMAIL_PASSWORD else 'NOT SET'}")
    
    if 'your-' in config.EMAIL_ADDRESS or 'your-' in config.EMAIL_PASSWORD:
        print("\n❌ ERROR: Email credentials not configured!")
        print("   Edit your .env file with real credentials")
        return False
    
    try:
        print("\nConnecting to IMAP server...")
        mail = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        print("✓ Connected to server")
        
        print("Attempting login...")
        mail.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
        print("✓ Login successful!")
        
        print("Listing folders...")
        status, folders = mail.list()
        if status == 'OK':
            print(f"✓ Found {len(folders)} folders")
            print("\nAvailable folders:")
            for folder in folders[:5]:  # Show first 5
                print(f"  - {folder.decode()}")
        
        mail.logout()
        print("\n✅ IMAP connection is working!")
        return True
        
    except imaplib.IMAP4.error as e:
        print(f"\n❌ IMAP Error: {e}")
        print("\nCommon fixes:")
        print("  1. Check email address is correct")
        print("  2. For Gmail: Use App Password, not regular password")
        print("     Create at: https://myaccount.google.com/apppasswords")
        print("  3. Enable IMAP in your email settings")
        print("  4. Check if 2FA is required")
        return False
    except Exception as e:
        print(f"\n❌ Connection Error: {e}")
        print("\nCommon fixes:")
        print("  1. Check IMAP_SERVER and IMAP_PORT in .env")
        print("  2. Verify firewall/network allows IMAP connections")
        print("  3. Try a different network if on VPN/proxy")
        return False


def test_smtp():
    """Test SMTP connection"""
    print("\n" + "=" * 60)
    print("TESTING SMTP CONNECTION")
    print("=" * 60)
    
    print(f"Server: {config.SMTP_SERVER}")
    print(f"Port: {config.SMTP_PORT}")
    print(f"Email: {config.EMAIL_ADDRESS}")
    
    try:
        print("\nConnecting to SMTP server...")
        
        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(config.SMTP_SERVER, config.SMTP_PORT)
            print("✓ Connected via SSL")
        else:
            server = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT)
            print("✓ Connected to server")
            print("Starting TLS...")
            server.starttls()
            print("✓ TLS started")
        
        print("Attempting login...")
        server.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
        print("✓ Login successful!")
        
        server.quit()
        print("\n✅ SMTP connection is working!")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"\n❌ Authentication Error: {e}")
        print("\nCommon fixes:")
        print("  1. Verify password is correct")
        print("  2. For Gmail: Use App Password")
        print("  3. Check if account has 2FA enabled")
        return False
    except Exception as e:
        print(f"\n❌ Connection Error: {e}")
        print("\nCommon fixes:")
        print("  1. Check SMTP_SERVER and SMTP_PORT in .env")
        print("  2. Try port 587 (TLS) or 465 (SSL)")
        print("  3. Verify network/firewall settings")
        return False


def show_config():
    """Display current configuration"""
    print("\n" + "=" * 60)
    print("CURRENT CONFIGURATION")
    print("=" * 60)
    
    print(f"Email Address: {config.EMAIL_ADDRESS}")
    print(f"Password Set: {'Yes' if config.EMAIL_PASSWORD else 'No'}")
    print(f"IMAP Server: {config.IMAP_SERVER}:{config.IMAP_PORT}")
    print(f"SMTP Server: {config.SMTP_SERVER}:{config.SMTP_PORT}")
    print(f"LLM Endpoint: {config.LLM_ENDPOINT}")
    print(f"LLM Model: {config.LLM_MODEL}")
    print(f"Dry Run: {config.DRY_RUN}")


def interactive_test():
    """Interactive testing with custom credentials"""
    print("\n" + "=" * 60)
    print("INTERACTIVE TEST MODE")
    print("=" * 60)
    print("Test with different credentials without changing .env\n")
    
    test_email = input(f"Email [{config.EMAIL_ADDRESS}]: ").strip() or config.EMAIL_ADDRESS
    test_password = getpass("Password (or App Password): ").strip()
    test_server = input(f"IMAP Server [{config.IMAP_SERVER}]: ").strip() or config.IMAP_SERVER
    test_port = input(f"IMAP Port [{config.IMAP_PORT}]: ").strip() or str(config.IMAP_PORT)
    
    try:
        test_port = int(test_port)
        print(f"\nTesting: {test_email} on {test_server}:{test_port}")
        
        mail = imaplib.IMAP4_SSL(test_server, test_port)
        mail.login(test_email, test_password)
        print("✅ Login successful!")
        mail.logout()
        
        print("\nTo use these settings, update your .env file:")
        print(f"EMAIL_ADDRESS={test_email}")
        print("EMAIL_PASSWORD=<your-password-or-app-password>")
        print(f"IMAP_SERVER={test_server}")
        print(f"IMAP_PORT={test_port}")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")


def main():
    """Main troubleshooting menu"""
    print("=" * 60)
    print("iCal Bot - Email Connection Troubleshooting")
    print("=" * 60)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--config':
            show_config()
            return
        elif sys.argv[1] == '--interactive':
            interactive_test()
            return
    
    # Run all tests
    show_config()
    
    # Test based on configured protocol
    if config.EMAIL_PROTOCOL == "EXCHANGE":
        exchange_ok = test_exchange()
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Exchange: {'✅ Working' if exchange_ok else '❌ Failed'}")
        
        if exchange_ok:
            print("\n🎉 Exchange connection working! You're ready to run the bot.")
        else:
            print("\n⚠️  Fix the issues above before running the bot.")
    else:
        imap_ok = test_imap()
        smtp_ok = test_smtp()
        
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"IMAP: {'✅ Working' if imap_ok else '❌ Failed'}")
        print(f"SMTP: {'✅ Working' if smtp_ok else '❌ Failed'}")
        
        if imap_ok and smtp_ok:
            print("\n🎉 All connections working! You're ready to run the bot.")
        else:
            print("\n⚠️  Fix the issues above before running the bot.")
            print("\nFor interactive testing:")
            print("  python troubleshoot.py --interactive")


if __name__ == "__main__":
    main()
