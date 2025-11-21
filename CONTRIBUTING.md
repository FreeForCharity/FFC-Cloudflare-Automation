# Contributing to FFC-Cloudflare-Automation

Thank you for your interest in contributing to this project! This guide will help you get started.

## How to Contribute

### Reporting Issues

If you encounter a bug or have a feature request:

1. Check the [existing issues](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues) to avoid duplicates
2. Create a new issue with:
   - Clear description of the problem or feature
   - Steps to reproduce (for bugs)
   - Expected vs. actual behavior
   - Your environment (Terraform version, OS, etc.)

### Submitting Pull Requests

1. **Fork the repository**
   ```bash
   git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
   cd FFC-Cloudflare-Automation-
   ```

2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make your changes**
   - Follow the existing code style
   - Update documentation if needed
   - Add comments for complex logic

4. **Test your changes**
   ```bash
   terraform fmt      # Format code
   terraform validate # Validate configuration
   terraform plan     # Test with sample values
   ```

5. **Commit your changes**
   ```bash
   git add .
   git commit -m "Add feature: description"
   ```

6. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

7. **Create a Pull Request**
   - Go to the original repository
   - Click "New Pull Request"
   - Select your fork and branch
   - Provide a clear description of your changes

## Development Guidelines

### Terraform Code Style

- Use meaningful variable and resource names
- Add comments for complex configurations
- Include validation for input variables
- Format code with `terraform fmt`
- Validate with `terraform validate`

### Documentation

- Update README.md for new features
- Update SETUP_GUIDE.md for new setup steps
- Include examples in code comments
- Keep documentation clear and concise

### Testing

Before submitting:

1. Run `terraform fmt` to format code
2. Run `terraform validate` to check syntax
3. Test with sample configurations (if possible)
4. Verify documentation accuracy

### Commit Messages

Use clear, descriptive commit messages:

```
Good: Add support for custom GitHub Pages IP addresses
Bad: Update files
```

Format:
```
[Type]: Brief description

Detailed explanation if needed
- Bullet points for multiple changes
- Reference issues: Fixes #123
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code formatting
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance tasks

## Code of Conduct

### Our Standards

- Be respectful and inclusive
- Welcome newcomers
- Accept constructive criticism
- Focus on what's best for the community
- Show empathy towards others

### Unacceptable Behavior

- Harassment or discrimination
- Trolling or insulting comments
- Personal or political attacks
- Publishing others' private information
- Other unprofessional conduct

## Questions?

- Open an issue for questions about the project
- Check existing documentation first
- Be specific and provide context

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

## Recognition

Contributors will be recognized in:
- GitHub contributors page
- Release notes (for significant contributions)
- Project documentation (for major features)

Thank you for contributing to FFC-Cloudflare-Automation!
